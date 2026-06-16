"""Revenue integrity — underpayment generation + the contract artifact (Day 1).

An underpaid line is the silent leak: the payer allows *less* than the contracted
rate and buries the shortfall in a bigger CO-45 write-off. The line still balances
and the deposit still reconciles to the cent — only a comparison against the
contracted fee schedule (contracts/fee_schedules.json) reveals the money withheld.

These tests pin Day-1 invariants: the contract artifact reproduces the legacy
factor math exactly (no regression), the generator emits flagged underpaid lines,
each line's arithmetic still holds, reconciliation still ties, and the golden set
records contracted/stated/recoverable truth.
"""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal

from app.contracts import contracted_allowed
from app.models import money
from gen.adjudicator import adjudicate_claim
from gen.builder import assemble
from gen.catalog import PAYERS
from gen.claim_factory import RatesConfig, build_claims
from gen.golden import build_golden

PAID_DATE = date(2026, 6, 1)

# The factors that used to live in gen/catalog.PAYER_FACTOR — the artifact must match.
LEGACY_FACTORS = {
    "DELTA DENTAL OF EXAMPLE": 0.70,
    "CIGNA DENTAL": 0.65,
    "AETNA DENTAL": 0.75,
    "METLIFE DENTAL": 0.68,
}


def _run(seed, claims, rates):
    rng = random.Random(seed)
    payer = rng.choice(PAYERS)
    raw = build_claims(rng, claims, rates, PAID_DATE)
    adj = [adjudicate_claim(rng, rc, payer) for rc in raw]
    remits = assemble(adj, payer, PAID_DATE)
    return payer, adj, remits


def test_contract_artifact_matches_legacy_factor():
    """No regression: contracted_allowed == min(factor*billed, billed), per payer."""
    for payer, factor in LEGACY_FACTORS.items():
        for billed in ("45.00", "70.00", "131.00", "900.00", "1500.00"):
            b = money(billed)
            expected = min(money(Decimal(str(factor)) * b), b)
            assert contracted_allowed(payer, "D1110", b) == expected, (payer, billed)


def test_generator_emits_flagged_underpaid_lines():
    _payer, adj, remits = _run(99, 20, RatesConfig(underpayment=0.5))
    underpaid = [(ac, i) for ac in adj for i in ac.notes if ac.notes[i].get("underpaid")]
    assert underpaid, "expected some underpaid lines at rate 0.5"
    # Invalid fixtures are impossible by construction — invariants + reconciliation hold.
    for r in remits:
        r.assert_valid()


def test_underpaid_line_math_and_recoverable():
    payer, adj, _ = _run(99, 20, RatesConfig(underpayment=0.5))
    checked = 0
    for ac in adj:
        for i, note in ac.notes.items():
            if not note.get("underpaid"):
                continue
            line = ac.claim.lines[i]
            contracted = money(note["contracted_allowed"])
            stated = money(note["stated_allowed"])
            recoverable = money(note["recoverable"])

            assert line.invariant_violations() == []          # line still balances
            assert line.allowed == stated                     # EOB shows the lower allowed
            assert stated < contracted                        # genuinely underpaid
            assert recoverable == money(contracted - stated)  # shortfall is the gap
            # The detector's truth: contract lookup − stated allowed == recoverable.
            assert recoverable == money(
                contracted_allowed(payer, line.cdt_code, line.billed) - line.allowed)
            checked += 1
    assert checked > 0


def test_reconciliation_still_ties_with_underpayments():
    """The killer proof: a contract break is NOT an arithmetic break. The deposit
    reconciles to the cent even though the payer underpaid."""
    _payer, adj, remits = _run(7, 15, RatesConfig(underpayment=0.6))
    has_underpaid = any(n.get("underpaid") for ac in adj for n in ac.notes.values())
    assert has_underpaid
    for r in remits:
        assert r.invariant_violations() == []   # eft_amount == Σ paid + PLB, exactly


def test_golden_records_underpayment_truth():
    _payer, adj, remits = _run(99, 20, RatesConfig(underpayment=0.5))
    golden = build_golden(adj, remits, 99)
    blocks = [ln for c in golden["claims"] for ln in c["lines"]
              if ln.get("expected_exception") == "underpayment"]
    assert blocks, "golden should flag underpaid lines"
    for ln in blocks:
        u = ln["underpayment"]
        assert money(u["stated_allowed"]) == money(ln["allowed"])
        assert money(u["recoverable"]) == money(
            money(u["contracted_allowed"]) - money(u["stated_allowed"]))
        assert money(u["recoverable"]) > 0


def test_clean_batch_has_no_underpayments():
    """No false positives: a batch with no underpayment rate flags nothing."""
    _payer, adj, remits = _run(42, 20, RatesConfig(denial=0.1))
    golden = build_golden(adj, remits, 42)
    flagged = [ln for c in golden["claims"] for ln in c["lines"]
               if ln.get("expected_exception") == "underpayment"]
    assert flagged == []
