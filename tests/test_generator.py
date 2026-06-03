"""Phase 0 — generator tests. Locks in the invariants, reproducibility, and that
each edge-case knob actually produces its labelled case.
"""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal

import pytest

from gen.adjudicator import adjudicate_claim
from gen.builder import assemble
from gen.catalog import PAYERS
from gen.claim_factory import (
    RatesConfig, build_claims,
    ROLE_COB, ROLE_DENIAL, ROLE_OVERPAYMENT, ROLE_REVERSAL, ROLE_SPLIT,
)
from gen.golden import build_golden

PAID_DATE = date(2026, 6, 1)


def _generate(seed: int, claims: int, rates: RatesConfig, plb=Decimal("0.00")):
    rng = random.Random(seed)
    payer = rng.choice(PAYERS)
    raw = build_claims(rng, claims, rates, PAID_DATE)
    adjudicated = [adjudicate_claim(rng, rc, payer) for rc in raw]
    remittances = assemble(adjudicated, payer, PAID_DATE, plb)
    return adjudicated, remittances


def test_invariants_hold_for_every_remittance():
    _, remittances = _generate(42, 30, RatesConfig(
        denial=0.1, reversal=0.05, cob=0.1, overpayment=0.05, split=0.1), plb=Decimal("-25.00"))
    for r in remittances:
        assert r.invariant_violations() == [], r.invariant_violations()


def test_reproducible_given_seed():
    a, ra = _generate(7, 15, RatesConfig(denial=0.1, cob=0.1))
    b, rb = _generate(7, 15, RatesConfig(denial=0.1, cob=0.1))
    assert build_golden(a, ra, 7) == build_golden(b, rb, 7)


@pytest.mark.parametrize("role,rates", [
    (ROLE_DENIAL, RatesConfig(denial=1.0)),
    (ROLE_REVERSAL, RatesConfig(reversal=1.0)),
    (ROLE_COB, RatesConfig(cob=1.0)),
    (ROLE_OVERPAYMENT, RatesConfig(overpayment=1.0)),
    (ROLE_SPLIT, RatesConfig(split=1.0)),
])
def test_each_edge_case_is_produced(role, rates):
    adjudicated, _ = _generate(3, 8, rates)
    assert any(ac.role == role for ac in adjudicated)


def test_denial_lines_are_zero_paid_and_status_4():
    adjudicated, _ = _generate(3, 8, RatesConfig(denial=1.0))
    for ac in adjudicated:
        assert ac.claim.clp_status_code == "4"
        assert all(line.paid == Decimal("0.00") for line in ac.claim.lines)


def test_overpayment_paid_exceeds_allowed():
    adjudicated, _ = _generate(3, 8, RatesConfig(overpayment=1.0))
    saw = False
    for ac in adjudicated:
        for line in ac.claim.lines:
            assert line.paid > line.allowed
            saw = True
    assert saw


def test_settled_golden_buckets_sum_to_billed():
    adjudicated, remittances = _generate(42, 30, RatesConfig(
        denial=0.1, reversal=0.05, cob=0.1, overpayment=0.05, split=0.1))
    g = build_golden(adjudicated, remittances, 42)
    for claim in g["claims"]:
        for line in claim["lines"]:
            if line["expected_status"] != "settled":
                continue
            s = line["expected_settlement"]
            total = (Decimal(s["insurance_paid"]) + Decimal(s["contractual_writeoff"])
                     + Decimal(s["patient_responsibility"]) + Decimal(s["secondary_responsibility"]))
            assert total == Decimal(line["billed"]), (claim["claim_id"], line["cdt_code"])


def test_reconciliation_eft_equals_claim_paid_plus_plb():
    _, remittances = _generate(42, 20, RatesConfig(split=0.1), plb=Decimal("-25.00"))
    for r in remittances:
        assert r.eft_amount == r.sum_claim_paid() + r.plb_amount
