"""FastAPI surface (PRD §11.2).

A read-mostly demonstration API over in-memory state — the durable Postgres
schema lives in ``app.tables`` for a real deployment, but the demo runs without a
database. On startup it loads (and processes) ``fixtures/run-42`` if present, so
the dashboard has data immediately. Uploads/process endpoints support interaction.

Routes:
  POST /remittances                  upload + ingest (835 or PDF) → remittance_id + lines
  POST /remittances/{id}/process     match → decide → settle → reconcile
  GET  /remittances                  list
  GET  /remittances/{id}/decisions   per-line AI decisions with citations + confidence
  GET  /remittances/{id}/reconciliation   reconciliation status, delta, breaks
  GET  /claims/{id}/settlement       settled amounts + action for a claim
  GET  /exceptions                   the triage queue
  POST /exceptions/{id}/resolve      accept recommendation or override → settles
  POST /eval/run                     run the golden-set harness; returns metrics
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.decide.cache import DecisionCache
from app.decide.chain import default_chain
from app.decide.decide import decide_adjustment
from app.exceptions import ExceptionQueue, ingest_results
from app.ingest import SeenRegistry, content_hash, detect_source_type
from app.kb.build import default_store
from app.match import OpenClaimRepository, match_remittance
from app.reconcile import reconcile
from app.settle import SettlementStore, settle_remittance

FIXTURE = Path("fixtures/run-42")


# ---------------- serialization helpers ----------------

def _settlement_dict(rec) -> dict:
    return {
        "claim_id": rec.claim_id, "claim_line_id": rec.claim_line_id, "cdt_code": rec.cdt_code,
        "billed": str(rec.billed), "insurance_paid": str(rec.insurance_paid),
        "contractual_writeoff": str(rec.contractual_writeoff),
        "patient_responsibility": str(rec.patient_responsibility),
        "secondary_responsibility": str(rec.secondary_responsibility),
        "appealed_open": str(rec.appealed_open), "action": rec.action, "status": rec.status,
        "fully_accounted": rec.fully_accounted(), "exceptions": rec.exceptions,
    }


def _recon_dict(r) -> dict:
    return {
        "remittance_id": r.remittance_id, "eft_amount": str(r.eft_amount),
        "sum_insurance_paid": str(r.sum_insurance_paid), "plb_amount": str(r.plb_amount),
        "delta": str(r.delta), "tied": r.tied, "held": r.held, "exceptions": r.exceptions,
    }


def _exception_dict(i) -> dict:
    return {
        "id": i.id, "reason": i.reason, "recommended_action": i.recommended_action,
        "claim_id": i.claim_id, "claim_line_id": i.claim_line_id, "cdt_code": i.cdt_code,
        "confidence": i.confidence, "evidence": i.evidence, "status": i.status,
        "resolution": (vars(i.resolution) if i.resolution else None),
    }


# ---------------- in-memory application state ----------------

class AppState:
    def __init__(self) -> None:
        self.store = default_store()
        self.cache = DecisionCache()
        self.chain = default_chain()
        self.repo = OpenClaimRepository()
        self.queue = ExceptionQueue()
        self.settlement_store = SettlementStore()
        self.seen = SeenRegistry()
        self.remittances: dict[str, dict] = {}     # trn -> {remit, source, processed, run}
        self.golden: Optional[dict] = None
        self._load_fixture()

    def _load_fixture(self) -> None:
        if not (FIXTURE / "golden.json").exists():
            return
        self.golden = json.loads((FIXTURE / "golden.json").read_text())
        self.repo = OpenClaimRepository.from_golden(self.golden)
        from app.parse import parse_835
        for edi in sorted(FIXTURE.glob("remit-*.835")):
            pr = parse_835(edi.read_text())
            self.seen.register(pr.content_hash)
            self.remittances[pr.remittance.trn] = {
                "remit": pr.remittance, "source": "835",
                "content_hash": pr.content_hash, "processed": False, "run": None,
                "parse_exceptions": pr.exceptions, "raw": edi.read_bytes(),
            }
        for trn in list(self.remittances):
            self.process(trn)

    def process(self, trn: str) -> dict:
        entry = self.remittances[trn]
        remit = entry["remit"]
        match_result = match_remittance(remit, self.repo)
        settlement = settle_remittance(remit, store=self.store, chain=self.chain, cache=self.cache)
        recon = reconcile(remit, settlement)
        ingest_results(self.queue, match_result=match_result, settlement=settlement, recon=recon)
        for e in entry.get("parse_exceptions", []):
            self.queue.add(e["reason"], evidence={"detail": e.get("detail")})
        committed = (recon.tied and not recon.held and not entry.get("parse_exceptions")
                     and not match_result.exceptions)  # fail-closed: hold if any line is unmatched
        if committed:
            self.settlement_store.commit(settlement)
        entry.update({"processed": True, "match": match_result, "settlement": settlement,
                      "recon": recon, "committed": committed})
        return entry

    def pipeline(self, trn: str) -> dict:
        """Per-stage status for the run timeline (the workflow, made visible)."""
        e = self.remittances[trn]
        remit = e["remit"]
        n_lines = sum(len(c.lines) for c in remit.claims)
        pexc = e.get("parse_exceptions", [])
        stages = [
            {"name": "Ingest", "status": "done",
             "summary": f"{e['source'].upper()} · #{e['content_hash'][:8]}",
             "detail": "detected type, hashed for idempotency"},
            {"name": "Parse / Extract", "status": "warn" if pexc else "done",
             "summary": f"{len(remit.claims)} claims · {n_lines} lines",
             "detail": f"{len(pexc)} arithmetic breaks" if pexc else "every line's math checks out"},
        ]
        if not e.get("processed"):
            for nm, d in [("Match", "link to open claims"), ("Decide", "rules + RAG"),
                          ("Settle", "record amounts"), ("Reconcile", "tie to EFT"),
                          ("Exceptions", "human review")]:
                stages.append({"name": nm, "status": "pending", "summary": "—", "detail": d})
            return {"remittance_id": trn, "committed": False, "stages": stages}

        m, s, r = e["match"], e["settlement"], e["recon"]
        mix: dict[str, int] = {}
        for d in self.decisions(trn):
            mix[d["source"]] = mix.get(d["source"], 0) + 1
        st: dict[str, int] = {}
        for rec in s.records:
            st[rec.status] = st.get(rec.status, 0) + 1
        claim_ids = {c.claim_id for c in remit.claims}
        exc = [i for i in self.queue.list("open")
               if (i.claim_line_id or "").startswith(trn) or i.claim_id in claim_ids]

        stages += [
            {"name": "Match", "status": "warn" if m.exceptions else "done",
             "summary": f"{m.matched_count}/{n_lines} matched",
             "detail": f"{len(m.exceptions)} unmatched" if m.exceptions else "all lines linked to a claim"},
            {"name": "Decide", "status": "done",
             "summary": f"{mix.get('rules', 0)} rules · {mix.get('rag', 0)} RAG",
             "detail": f"{mix.get('escalate', 0)} escalated · only denials reach the model"},
            {"name": "Settle", "status": "warn" if st.get("queued") else "done",
             "summary": f"{len(s.records)} lines settled",
             "detail": " · ".join(f"{k}:{v}" for k, v in st.items()) or "—"},
            {"name": "Reconcile", "status": "done" if r.tied else "fail",
             "summary": f"delta ${r.delta}",
             "detail": "Σ paid + PLB == EFT, tied to the cent" if r.tied else "BREAK — held"},
            {"name": "Exceptions", "status": "warn" if exc else "done",
             "summary": f"{len(exc)} open",
             "detail": "held for a human, with evidence" if exc else "nothing to review"},
        ]
        return {"remittance_id": trn, "committed": e.get("committed", False), "stages": stages}

    def decisions(self, trn: str) -> list[dict]:
        remit = self.remittances[trn]["remit"]
        out = []
        for claim in remit.claims:
            for line in claim.lines:
                for adj in line.adjustments:
                    res = decide_adjustment(adj.group_code, adj.reason_code, adj.amount,
                                            remit.payer, line.cdt_code, claim.clp_status_code,
                                            store=self.store, chain=self.chain, cache=self.cache)
                    out.append({
                        "claim_id": claim.claim_id, "cdt_code": line.cdt_code,
                        "group_code": adj.group_code, "reason_code": adj.reason_code,
                        "amount": str(adj.amount), "action": res.action, "source": res.source,
                        "confidence": res.confidence, "citations": res.citations,
                        "rationale": res.rationale, "escalated": res.escalate, "reason": res.reason,
                    })
        return out


_state: Optional[AppState] = None


def state() -> AppState:
    global _state
    if _state is None:
        _state = AppState()
    return _state


# ---------------- app + routes ----------------

app = FastAPI(title="Remit", description="AI remittance adjudication engine", version="3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health():
    return {"status": "ok", "remittances": len(state().remittances)}


@app.post("/remittances")
async def upload_remittance(file: UploadFile = File(...)):
    data = await file.read()
    h = content_hash(data)
    if state().seen.is_duplicate(h):
        raise HTTPException(409, "duplicate remittance (content hash already ingested)")
    source = detect_source_type(file.filename, data)
    if source == "835":
        from app.parse import parse_835
        pr = parse_835(data.decode())
        remit, pexc = pr.remittance, pr.exceptions
    elif source == "pdf":
        import tempfile
        from app.extract import extract_pdf
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(data)
            er = extract_pdf(tmp.name)
        remit, pexc = er.remittance, er.exceptions
    else:
        raise HTTPException(415, "unrecognized file type (expected 835 or PDF)")

    state().seen.register(h)
    state().remittances[remit.trn] = {"remit": remit, "source": source, "content_hash": h,
                                      "processed": False, "run": None, "parse_exceptions": pexc,
                                      "raw": data}
    return {"remittance_id": remit.trn, "source": source, "payer": remit.payer,
            "claims": len(remit.claims), "lines": sum(len(c.lines) for c in remit.claims),
            "parse_exceptions": pexc}


@app.post("/remittances/{trn}/process")
def process_remittance_route(trn: str):
    if trn not in state().remittances:
        raise HTTPException(404, "remittance not found")
    entry = state().process(trn)
    return {"remittance_id": trn, "committed": entry["committed"],
            "matched": entry["match"].matched_count,
            "unmatched": len(entry["match"].exceptions),
            "reconciliation": _recon_dict(entry["recon"])}


@app.get("/remittances")
def list_remittances():
    out = []
    for trn, e in state().remittances.items():
        out.append({"remittance_id": trn, "payer": e["remit"].payer, "source": e["source"],
                    "eft_amount": str(e["remit"].eft_amount), "claims": len(e["remit"].claims),
                    "processed": e["processed"], "committed": e.get("committed", False),
                    "tied": (e["recon"].tied if e.get("recon") else None)})
    return out


@app.get("/claims")
def list_claims():
    """The office's open claims — what the Match stage links remittance lines back to."""
    return [{"claim_id": ln.claim_id, "patient_ref": ln.patient_ref, "cdt_code": ln.cdt_code,
             "date_of_service": str(ln.date_of_service), "payer": ln.payer,
             "billed": str(ln.billed), "status": ln.status} for ln in state().repo.open_lines()]


@app.post("/claims")
async def upload_claims(file: UploadFile = File(...)):
    """Seed the office's open claims (stand-in for the PMS / 837 submission) so remittances can match."""
    data = await file.read()
    try:
        golden = json.loads(data.decode())
    except Exception:
        raise HTTPException(415, "claims file must be JSON in golden format: {payer, claims:[...]}")
    if "claims" not in golden:
        raise HTTPException(422, "expected a 'claims' array in the file")
    added = state().repo.extend_from_golden(golden)
    return {"added": added, "total_open_lines": len(state().repo.open_lines())}


SAMPLES = [
    {"id": "clean", "label": "Clean batch", "dir": "fixtures/sample/clean",
     "description": "A straightforward batch — every line matches and the money posts cleanly."},
    {"id": "denials", "label": "Denials & appeals", "dir": "fixtures/sample/denials",
     "description": "Includes denials the AI must interpret — generates items to review in the inbox."},
]


@app.get("/samples")
def list_samples():
    """Curated claim + remittance cohorts a visitor can load and play with."""
    out = []
    for s in SAMPLES:
        try:
            g = json.loads((Path(s["dir"]) / "golden.json").read_text())
        except Exception:
            continue
        out.append({"id": s["id"], "label": s["label"], "description": s["description"],
                    "payer": g.get("payer", ""), "claims": len(g.get("claims", []))})
    return out


@app.post("/samples/{sample_id}/load")
def load_sample(sample_id: str):
    s = next((x for x in SAMPLES if x["id"] == sample_id), None)
    if not s:
        raise HTTPException(404, "unknown sample")
    d = Path(s["dir"])
    from app.parse import parse_835
    edi = d / "remit-001.835"
    pr = parse_835(edi.read_text())
    trn = pr.remittance.trn
    if trn not in state().remittances:  # idempotent: re-loading just re-selects it
        golden = json.loads((d / "golden.json").read_text())
        state().repo.extend_from_golden(golden)         # seed the matching claims
        state().seen.register(pr.content_hash)
        state().remittances[trn] = {"remit": pr.remittance, "source": "835",
                                    "content_hash": pr.content_hash, "processed": False, "run": None,
                                    "parse_exceptions": pr.exceptions, "raw": edi.read_bytes()}
        state().process(trn)
    return {"remittance_id": trn, "label": s["label"]}


@app.get("/remittances/{trn}/source")
def get_source(trn: str):
    """Serve the original uploaded document (PDF or raw 835) — document-in, data-out."""
    from fastapi.responses import Response
    e = state().remittances.get(trn)
    if not e or not e.get("raw"):
        raise HTTPException(404, "no source document stored for this remittance")
    media = "application/pdf" if e["source"] == "pdf" else "text/plain"
    return Response(content=e["raw"], media_type=media)


@app.get("/remittances/{trn}/pipeline")
def get_pipeline(trn: str):
    if trn not in state().remittances:
        raise HTTPException(404, "remittance not found")
    return state().pipeline(trn)


@app.get("/remittances/{trn}/decisions")
def get_decisions(trn: str):
    if trn not in state().remittances:
        raise HTTPException(404, "remittance not found")
    return state().decisions(trn)


@app.get("/remittances/{trn}/reconciliation")
def get_reconciliation(trn: str):
    e = state().remittances.get(trn)
    if not e or not e.get("recon"):
        raise HTTPException(404, "not processed")
    return _recon_dict(e["recon"])


@app.get("/claims/{claim_id}/settlement")
def get_claim_settlement(claim_id: str):
    out = []
    for e in state().remittances.values():
        if not e.get("settlement"):
            continue
        out += [_settlement_dict(r) for r in e["settlement"].records if r.claim_id == claim_id]
    if not out:
        raise HTTPException(404, "no settlement for claim")
    return out


@app.get("/exceptions")
def get_exceptions(status: str = "open"):
    items = state().queue.list(status if status != "all" else None)
    return [_exception_dict(i) for i in items]


class ResolveBody(BaseModel):
    decision: str          # accept | override
    action: Optional[str] = None
    operator: str = "operator"


@app.post("/exceptions/{item_id}/resolve")
def resolve_exception(item_id: str, body: ResolveBody):
    try:
        res = state().queue.resolve(item_id, body.decision, action=body.action, operator=body.operator)
    except KeyError:
        raise HTTPException(404, "exception not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return vars(res)


@app.post("/eval/run")
def run_eval_route():
    if not state().golden:
        raise HTTPException(400, "no golden set loaded")
    from eval.pipeline_eval import run_pipeline_eval
    return run_pipeline_eval(str(FIXTURE))
