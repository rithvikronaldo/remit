"""Revenue integrity — the underpayment detector (Day 2).

Day 1 built the leak (the generator buries a below-contract payment in a bigger
CO-45) and the contract artifact. Day 2 is the engine side: compare each line's
stated ``allowed`` against ``contracted_allowed()`` and flag the shortfall as
recoverable revenue.

The headline property under test: **the deposit reconciles to the cent AND the
detector still catches the leak** — an underpayment is a contract break, not an
arithmetic break, so the arithmetic gate and reconciliation are structurally
blind to it. The detector is also deliberately conservative: no contract on
file → no comparison; denials/COB/reversals are other workflows; a cent of
rounding slack never flags.
"""

from __future__ import annotations

import os

os.environ["REMIT_EMBEDDER"] = "hash"

import json
import random
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.detect import apply_findings, detect_underpayments
from app.exceptions import ExceptionQueue
from app.match import OpenClaimRepository
from app.models import Claim, ClaimLine, Remittance, money
from app.parse import parse_835
from app.pipeline import process_remittance
from app.reconcile import reconcile
from app.settle import SettlementStore, settle_remittance
from gen.adjudicator import adjudicate_claim
from gen.builder import assemble
from gen.catalog import PAYERS
from gen.claim_factory import RatesConfig, build_claims
from gen.cli import main as gen_main
from gen.golden import build_golden

PAID_DATE = date(2026, 6, 1)
ZERO = money(0)


def _run(seed, claims, rates):
    rng = random.Random(seed)
    payer = rng.choice(PAYERS)
    raw = build_claims(rng, claims, rates, PAID_DATE)
    adj = [adjudicate_claim(rng, rc, payer) for rc in raw]
    remits = assemble(adj, payer, PAID_DATE)
    return payer, adj, remits


def _mk_remit(payer: str, *, allowed, billed="100.00", status="1") -> Remittance:
    line = ClaimLine(cdt_code="D1110", billed=billed, allowed=allowed, paid=allowed,
                     adjustments=[], patient_responsibility="0.00")
    claim = Claim(claim_id="CLM1", patient_ref="PAT1", date_of_service=PAID_DATE,
                  clp_status_code=status, billed_total=billed, paid_total=allowed,
                  lines=[line])
    return Remittance(payer=payer, trn="EFT-TEST-1", payment_method="ACH",
                      eft_amount=allowed, paid_date=PAID_DATE, claims=[claim])


def test_detector_flags_exactly_the_generator_truth():
    """No false positives, no false negatives: findings == the generator's notes,
    and each recoverable equals the buried shortfall."""
    _payer, adj, remits = _run(99, 20, RatesConfig(underpayment=0.5))
    truth = {(ac.claim.claim_id, i): note
             for ac in adj for i, note in ac.notes.items() if note.get("underpaid")}
    assert truth, "seed should produce underpaid lines"

    found = {}
    for r in remits:
        for f in detect_underpayments(r).findings:
            found[(f.claim_id, f.line_index)] = f

    assert set(found) == set(truth)
    for key, note in truth.items():
        f = found[key]
        assert f.recoverable == money(note["recoverable"])
        assert f.contracted_allowed == money(note["contracted_allowed"])
        assert f.stated_allowed == money(note["stated_allowed"])


def test_leak_is_invisible_to_settle_but_caught_by_contract():
    """Settlement alone settles every underpaid line (that's the blind spot);
    the detector queues them, and the batch is held from commit — fail-closed."""
    _payer, _adj, remits = _run(99, 12, RatesConfig(underpayment=1.0))
    remit = remits[0]

    settlement = settle_remittance(remit)
    flagged_before = [r for r in settlement.records if r.status == "queued"]
    assert flagged_before == []            # the leak is invisible to settlement

    result = detect_underpayments(remit)
    assert result.contract_on_file and result.findings
    apply_findings(settlement, result)

    queued = [r for r in settlement.records if r.status == "queued"]
    assert len(queued) == len(result.findings)
    for rec in queued:
        assert rec.action == "review"
        assert rec.exceptions[0]["reason"] == "underpayment"
        assert rec.fully_accounted()       # buckets untouched — still conserves

    recon = reconcile(remit, settlement)
    assert recon.tied and recon.delta == ZERO   # the deposit ties to the cent
    assert recon.held                            # ... but the batch is held
    # The pre-commit gate refuses a held remittance: nothing is written.
    from app.reconcile import commit_if_reconciled
    _, written = commit_if_reconciled(SettlementStore(), remit, settlement)
    assert written == 0


def test_headline_reconciles_to_cent_and_flags_the_leak():
    """THE headline: run the full pipeline on a generated batch. Every remittance
    reconciles to the cent — and the detector still surfaces the recoverable $,
    queued for a human with the contract math attached."""
    with tempfile.TemporaryDirectory() as tmp:
        gen_main(["--seed", "77", "--claims", "14", "--underpayment-rate", "0.4",
                  "--out", tmp])
        golden = json.loads((Path(tmp) / "golden.json").read_text())
        repo = OpenClaimRepository.from_golden(golden)
        queue = ExceptionQueue()

        runs = []
        for edi in sorted(Path(tmp).glob("remit-*.835")):
            pr = parse_835(edi.read_text())
            runs.append(process_remittance(pr.remittance, repo, queue,
                                           content_hash_value=pr.content_hash,
                                           parse_exceptions=pr.exceptions))

        # 1) Arithmetic is perfect everywhere: every deposit ties to the cent.
        for run in runs:
            assert run.reconciliation.tied
            assert run.reconciliation.delta == ZERO

        # 2) The detector still catches the leak.
        findings = [f for run in runs for f in run.underpayment.findings]
        assert findings, "fixture should contain underpaid lines"

        # 3) Golden agreement: same lines, same recoverable total.
        expected = [l for c in golden["claims"] for l in c["lines"]
                    if l.get("expected_exception") == "underpayment"]
        assert len(findings) == len(expected)
        expected_total = money(sum((money(l["underpayment"]["recoverable"])
                                    for l in expected), Decimal("0")))
        found_total = money(sum((f.recoverable for f in findings), Decimal("0")))
        assert found_total == expected_total > ZERO

        # 4) Fail-closed: a flagged batch ties but is held, never auto-committed.
        for run in runs:
            if run.underpayment.findings:
                assert run.reconciliation.held and not run.committed
            else:
                assert run.committed

        # 5) The human sees an actionable item with the contract math attached.
        items = [i for i in queue.open_items() if i.reason == "underpayment"]
        assert len(items) == len(findings)
        queue_total = ZERO
        for item in items:
            assert item.recommended_action == "review"
            exc = item.evidence["line_exceptions"][0]
            assert money(exc["contracted_allowed"]) > money(exc["stated_allowed"])
            queue_total = money(queue_total + money(exc["recoverable"]))
        assert queue_total == expected_total


def test_no_contract_no_comparison():
    """A payer with no fee schedule on file is never judged against the default
    factor — no contract, no evidence, no finding."""
    remit = _mk_remit("ACME INSURANCE (NO CONTRACT)", allowed="10.00")
    result = detect_underpayments(remit)
    assert not result.contract_on_file
    assert result.findings == [] and result.lines_checked == 0


def test_skips_denials_cob_and_reversals():
    """Non-primary statuses are other workflows (appeal / re-adjudication /
    negation) — a low stated allowed there is not an underpayment."""
    for status in ("4", "2", "22"):
        remit = _mk_remit("DELTA DENTAL OF EXAMPLE", allowed="0.00", status=status)
        assert detect_underpayments(remit).findings == [], f"status {status}"
    # And a generated batch full of those roles, with no underpayments, is silent.
    _payer, _adj, remits = _run(3, 20, RatesConfig(denial=0.3, cob=0.2, reversal=0.1))
    for r in remits:
        assert detect_underpayments(r).findings == []


def test_tolerance_swallows_rounding_noise():
    """DELTA contract = 70% of billed → $70.00 on $100. A cent under is noise;
    two cents is a finding."""
    payer = "DELTA DENTAL OF EXAMPLE"
    assert detect_underpayments(_mk_remit(payer, allowed="69.99")).findings == []
    findings = detect_underpayments(_mk_remit(payer, allowed="69.98")).findings
    assert len(findings) == 1
    assert findings[0].recoverable == money("0.02")
    assert findings[0].contracted_allowed == money("70.00")


def test_pipeline_eval_meets_targets_with_underpayments():
    """The whole-pipeline eval scores an underpayment fixture perfectly: settle
    buckets match golden, flagged lines are exactly the expected queued set."""
    with tempfile.TemporaryDirectory() as tmp:
        gen_main(["--seed", "11", "--claims", "12", "--underpayment-rate", "0.35",
                  "--denial-rate", "0.1", "--cob-rate", "0.1", "--out", tmp])
        from eval.pipeline_eval import run_pipeline_eval
        report = run_pipeline_eval(tmp)
        assert report["settlement_accuracy"] == 1.0
        assert report["reconciliation_pass_rate"] == 1.0
        assert report["exception_recall"] == 1.0
        assert report["exception_precision"] == 1.0
        assert report["meets_targets"]
