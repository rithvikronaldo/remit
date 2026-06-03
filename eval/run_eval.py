"""Phase 2 eval — score the decision layer against a golden set.

Flattens the golden's claims → lines → adjustments to per-adjustment cases, runs
``decide_adjustment``, and reports the PRD §5.2 metrics. Tagged with
``(model, prompt_version, corpus_version)`` and persisted so any prompt/corpus
change is diffable.

A decision of action "review" and any escalation both mean "send to a human", so
both are normalized to the "escalate" bucket for scoring (the golden labels
should-review adjustments with expected_action "review").

    python -m eval.run_eval --fixture fixtures/run-42
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Optional

from app.decide.cache import DecisionCache
from app.decide.chain import MODEL, PROMPT_VERSION, default_chain
from app.decide.decide import decide_adjustment
from app.kb.build import CORPUS_VERSION

NEEDS_HUMAN = "escalate"


def _norm(action: Optional[str], escalated: bool) -> str:
    if escalated or action == "review":
        return NEEDS_HUMAN
    return action or NEEDS_HUMAN


@dataclass
class Case:
    claim_id: str
    cdt_code: str
    group: str
    reason: str
    amount: str
    payer: Optional[str]
    claim_status: str
    expected_action: str
    is_denial: bool


def load_cases(golden: dict) -> list[Case]:
    payer = golden.get("payer")
    cases: list[Case] = []
    for claim in golden["claims"]:
        status = claim["clp_status_code"]
        is_denial = claim.get("role") == "denial" or status == "4"
        for line in claim["lines"]:
            for adj in line["adjustments"]:
                cases.append(Case(
                    claim_id=claim["claim_id"], cdt_code=line["cdt_code"],
                    group=adj["group"], reason=adj["reason"], amount=adj["amount"],
                    payer=payer, claim_status=status,
                    expected_action=adj["expected_action"], is_denial=is_denial,
                ))
    return cases


@dataclass
class Row:
    expected: str
    predicted: str
    is_denial: bool
    source: str
    citations_ok: bool


def run_eval(golden: dict, *, chain=None, cache=None, store=None, threshold=None) -> dict:
    chain = chain or default_chain()
    cache = cache if cache is not None else DecisionCache()
    rows: list[Row] = []

    for case in load_cases(golden):
        res = decide_adjustment(
            case.group, case.reason, case.amount, case.payer, case.cdt_code, case.claim_status,
            store=store, chain=chain, cache=cache, threshold=threshold,
        )
        predicted = _norm(res.action, res.escalate)
        expected = _norm(case.expected_action, False)
        # Citations valid for RAG decisions: present (rules carry rule:* citations too).
        citations_ok = bool(res.citations)
        rows.append(Row(expected, predicted, case.is_denial, res.source, citations_ok))

    return summarize(rows, chain)


def summarize(rows: list[Row], chain) -> dict:
    n = len(rows) or 1
    correct = sum(r.expected == r.predicted for r in rows)
    denials = [r for r in rows if r.is_denial]
    denial_correct = sum(r.expected == r.predicted for r in denials)
    rag = [r for r in rows if r.source == "rag"]
    rag_cited = sum(r.citations_ok for r in rag)

    # Escalation precision/recall against the "needs human" bucket.
    pred_esc = [r for r in rows if r.predicted == NEEDS_HUMAN]
    exp_esc = [r for r in rows if r.expected == NEEDS_HUMAN]
    esc_tp = sum(r.expected == NEEDS_HUMAN for r in pred_esc)
    esc_precision = esc_tp / (len(pred_esc) or 1)
    esc_recall = esc_tp / (len(exp_esc) or 1)

    tier_mix: dict[str, int] = {}
    for r in rows:
        tier_mix[r.source] = tier_mix.get(r.source, 0) + 1

    return {
        "version": {"model": getattr(chain, "name", MODEL),
                    "prompt_version": PROMPT_VERSION, "corpus_version": CORPUS_VERSION},
        "n_adjustments": len(rows),
        "decision_accuracy": round(correct / n, 4),
        # None (not 0.0) when the category is absent, so the gate treats it as N/A.
        "denial_action_accuracy": round(denial_correct / len(denials), 4) if denials else None,
        "denials": len(denials),
        "citation_coverage": round(rag_cited / len(rag), 4) if rag else None,
        "escalation_precision": round(esc_precision, 4),
        "escalation_recall": round(esc_recall, 4),
        "tier_mix": tier_mix,
        "mismatches": [
            {"expected": r.expected, "predicted": r.predicted, "is_denial": r.is_denial, "source": r.source}
            for r in rows if r.expected != r.predicted
        ],
    }


# PRD §5.2 regression targets.
TARGETS = {"decision_accuracy": 0.95, "denial_action_accuracy": 0.95, "citation_coverage": 1.0}


def meets_targets(report: dict) -> bool:
    for k, target in TARGETS.items():
        val = report.get(k)
        if val is None:          # category not present in this fixture → N/A
            continue
        if val < target:
            return False
    return True


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="eval.run_eval")
    p.add_argument("--fixture", default="fixtures/run-42")
    p.add_argument("--out", default=None, help="write the JSON report here")
    args = p.parse_args(argv)

    golden = json.loads((Path(args.fixture) / "golden.json").read_text())
    report = run_eval(golden)

    print(json.dumps({k: v for k, v in report.items() if k != "mismatches"}, indent=2))
    if report["mismatches"]:
        print(f"\n{len(report['mismatches'])} mismatches:")
        for m in report["mismatches"][:20]:
            print(f"  expected={m['expected']:<20} predicted={m['predicted']:<20} "
                  f"denial={m['is_denial']} source={m['source']}")

    ok = meets_targets(report)
    print(f"\n{'✅ MEETS' if ok else '❌ BELOW'} targets {TARGETS}")
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
