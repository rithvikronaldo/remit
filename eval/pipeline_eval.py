"""Phase 9 — whole-pipeline eval.

Generalises the Phase 2 decision eval to the full pipeline: for a fixture, run
parse → match → settle → reconcile (and PDF extract), then compare the final
settlements, the per-line decisions, and the reconciliation result to the
fixture's ground truth. Tagged with (model, prompt_version, corpus_version),
persisted, and gated for CI.

    python -m eval.pipeline_eval --fixture fixtures/run-42
"""

from __future__ import annotations

import argparse
import json
import statistics
from decimal import Decimal
from pathlib import Path

from app.decide.cache import DecisionCache
from app.decide.chain import MODEL, PROMPT_VERSION, default_chain
from app.exceptions import ExceptionQueue
from app.extract import extract_pdf
from app.kb.build import CORPUS_VERSION
from app.match import OpenClaimRepository
from app.models import money
from app.observability import log_event, write_run_report
from app.parse import parse_835
from app.pipeline import process_remittance
from eval.run_eval import run_eval as decision_eval

# Regression targets (PRD §5.2). Pipeline gate is the money-correctness subset.
TARGETS = {
    "decision_accuracy": 0.95,
    "denial_action_accuracy": 0.95,
    "settlement_accuracy": 1.0,
    "reconciliation_pass_rate": 1.0,
    "extraction_accuracy": 0.98,
}


def _settlement_accuracy(golden: dict, settlement_records: dict) -> float:
    total = correct = 0
    recs = {k: list(v) for k, v in settlement_records.items()}
    for claim in golden["claims"]:
        for line in claim["lines"]:
            key = (claim["claim_id"], line["cdt_code"])
            cands = recs.get(key)
            if not cands:
                total += 1
                continue
            rec = cands.pop(0)
            exp = line["expected_settlement"]
            ok = (rec.insurance_paid == money(exp["insurance_paid"])
                  and rec.contractual_writeoff == money(exp["contractual_writeoff"])
                  and rec.patient_responsibility == money(exp["patient_responsibility"])
                  and rec.secondary_responsibility == money(exp["secondary_responsibility"])
                  and rec.status == line["expected_status"])
            total += 1
            correct += ok
    return correct / total if total else 0.0


def _extraction_accuracy(fixture: Path) -> float | None:
    pdfs = sorted(fixture.glob("eob-*.pdf"))
    e835 = sorted(fixture.glob("remit-*.835"))
    if not pdfs:
        return None
    total = correct = 0
    for pdf, edi in zip(pdfs, e835):
        from_pdf = extract_pdf(str(pdf)).remittance
        from_edi = parse_835(edi.read_text()).remittance
        for pc, ec in zip(from_pdf.claims, from_edi.claims):
            for pl, el in zip(pc.lines, ec.lines):
                for f in ("billed", "allowed", "paid", "patient_responsibility"):
                    total += 1
                    correct += getattr(pl, f) == getattr(el, f)
    return correct / total if total else None


def run_pipeline_eval(fixture: str) -> dict:
    fx = Path(fixture)
    golden = json.loads((fx / "golden.json").read_text())
    repo = OpenClaimRepository.from_golden(golden)
    queue = ExceptionQueue()
    cache = DecisionCache()
    chain = default_chain()

    settlement_records: dict[tuple, list] = {}
    tied = runs = 0
    latencies: list[float] = []

    for edi in sorted(fx.glob("remit-*.835")):
        pr = parse_835(edi.read_text())
        run = process_remittance(pr.remittance, repo, queue, chain=chain, cache=cache,
                                 content_hash_value=pr.content_hash, parse_exceptions=pr.exceptions)
        runs += 1
        tied += int(run.reconciliation.tied)
        latencies.append(run.latency_ms)
        for rec in run.settlement.records:
            settlement_records.setdefault((rec.claim_id, rec.cdt_code), []).append(rec)
        log_event("remittance_processed", trn=run.remittance_id, tied=run.reconciliation.tied,
                  held=run.reconciliation.held, latency_ms=run.latency_ms)

    decision = decision_eval(golden, chain=chain, cache=cache)

    # Exception precision/recall on "should be flagged" (golden expected_status == queued).
    queued_pred = {(r.claim_id, r.cdt_code) for recs in settlement_records.values() for r in recs
                   if r.status == "queued"}
    queued_exp = {(c["claim_id"], l["cdt_code"]) for c in golden["claims"] for l in c["lines"]
                  if l["expected_status"] == "queued"}
    tp = len(queued_pred & queued_exp)
    exc_precision = tp / (len(queued_pred) or 1)
    exc_recall = tp / (len(queued_exp) or 1)

    report = {
        "version": {"model": getattr(chain, "name", MODEL),
                    "prompt_version": PROMPT_VERSION, "corpus_version": CORPUS_VERSION},
        "fixture": str(fx),
        "remittances": runs,
        "decision_accuracy": decision["decision_accuracy"],
        "denial_action_accuracy": decision["denial_action_accuracy"],
        "citation_coverage": decision["citation_coverage"],
        "settlement_accuracy": round(_settlement_accuracy(golden, settlement_records), 4),
        "reconciliation_pass_rate": round(tied / (runs or 1), 4),
        "extraction_accuracy": _extraction_accuracy(fx),
        "exception_precision": round(exc_precision, 4),
        "exception_recall": round(exc_recall, 4),
        "latency_ms_median": round(statistics.median(latencies), 2) if latencies else 0.0,
        "open_exceptions": len(queue.open_items()),
        "tier_mix": decision["tier_mix"],
        "targets": TARGETS,
    }
    report["meets_targets"] = meets_targets(report)
    return report


def meets_targets(report: dict) -> bool:
    for k, target in TARGETS.items():
        val = report.get(k)
        if val is None:          # metric not applicable (e.g. no PDFs) → skip
            continue
        if val < target:
            return False
    return True


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="eval.pipeline_eval")
    p.add_argument("--fixture", default="fixtures/run-42")
    args = p.parse_args(argv)

    report = run_pipeline_eval(args.fixture)
    paths = write_run_report(report)
    print(json.dumps({k: v for k, v in report.items() if k != "targets"}, indent=2))
    print(f"\n{'✅ MEETS' if report['meets_targets'] else '❌ BELOW'} targets → report: {paths['html']}")
    return 0 if report["meets_targets"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
