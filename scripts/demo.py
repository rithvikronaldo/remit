"""Scripted end-to-end demo narration.

    remittance in → RAG interprets each line with citations → settled and
    reconciled to the cent → the one denial is queued.

Runs entirely offline/free (local embeddings + the metadata-stub decision chain
when no ANTHROPIC_API_KEY is set). Generates fixtures/run-42 if missing.

    make demo      # or:  python -m scripts.demo
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.decide.cache import DecisionCache
from app.decide.chain import default_chain
from app.decide.decide import decide_adjustment
from app.exceptions import ExceptionQueue, ingest_results
from app.kb.build import default_store
from app.match import OpenClaimRepository, match_remittance
from app.parse import parse_835
from app.reconcile import reconcile
from app.settle import SettlementStore, settle_remittance

FIXTURE = Path("fixtures/run-42")
BAR = "─" * 64


def _ensure_fixture() -> None:
    if (FIXTURE / "golden.json").exists():
        return
    print("· generating fixtures/run-42 …")
    subprocess.run([sys.executable, "-m", "gen", "--seed", "42", "--claims", "20",
                    "--denial-rate", "0.1", "--cob-rate", "0.1", "--overpayment-rate", "0.05",
                    "--split-rate", "0.1", "--plb", "--pdf", "--out", str(FIXTURE)], check=True)


def main() -> int:
    _ensure_fixture()
    golden = json.loads((FIXTURE / "golden.json").read_text())
    repo = OpenClaimRepository.from_golden(golden)
    store, cache, chain = default_store(), DecisionCache(), default_chain()
    queue = ExceptionQueue()
    settlement_store = SettlementStore()

    print(f"\n{BAR}\n  REMIT — AI remittance adjudication (demo)\n  decision model: {getattr(chain, 'name', '?')}\n{BAR}")

    for edi in sorted(FIXTURE.glob("remit-*.835")):
        pr = parse_835(edi.read_text())
        remit = pr.remittance
        print(f"\n▶ {edi.name}  payer={remit.payer}  TRN={remit.trn}  EFT=${remit.eft_amount}")

        m = match_remittance(remit, repo)
        print(f"  matched {m.matched_count} lines · unmatched {len(m.exceptions)}")

        # Show a couple of cited RAG interpretations (denials/ambiguous).
        shown = 0
        for claim in remit.claims:
            for line in claim.lines:
                for adj in line.adjustments:
                    res = decide_adjustment(adj.group_code, adj.reason_code, adj.amount, remit.payer,
                                            line.cdt_code, claim.clp_status_code,
                                            store=store, chain=chain, cache=cache)
                    if res.source == "rag" and shown < 3:
                        cites = ", ".join(res.citations)
                        print(f"    {claim.claim_id} {line.cdt_code} {adj.group_code}-{adj.reason_code} "
                              f"→ {res.action}  (conf {res.confidence}; cites: {cites})")
                        shown += 1

        s = settle_remittance(remit, store=store, chain=chain, cache=cache)
        recon = reconcile(remit, s)
        ingest_results(queue, match_result=m, settlement=s, recon=recon)
        committed = recon.tied and not recon.held
        if committed:
            settlement_store.commit(s)

        tick = "✓" if recon.tied else "✗"
        print(f"  reconcile: Σpaid ${recon.sum_insurance_paid} + PLB ${recon.plb_amount} "
              f"vs EFT ${recon.eft_amount}  → delta ${recon.delta} {tick}"
              f"  ({'committed' if committed else 'HELD for review'})")

    print(f"\n{BAR}\n  EXCEPTION QUEUE ({len(queue.open_items())} open)\n{BAR}")
    for item in queue.open_items():
        print(f"  • {item.reason}  {item.claim_id or ''} {item.cdt_code or ''}"
              f"  → recommend: {item.recommended_action}")

    print(f"\n  committed settlement records: {len(settlement_store)}")
    print(f"{BAR}\n  Done. Run `make pipeline OUT=fixtures/run-42` for scored metrics.\n{BAR}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
