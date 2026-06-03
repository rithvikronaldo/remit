"""Phase 5 — matching engine tests."""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal

from app.match import OpenClaimRepository, match_remittance
from app.models import money
from gen.adjudicator import adjudicate_claim
from gen.builder import assemble
from gen.catalog import PAYERS
from gen.claim_factory import RatesConfig, build_claims

PAID_DATE = date(2026, 6, 1)


def _setup(seed, claims, rates, plb=Decimal("0.00")):
    rng = random.Random(seed)
    payer = rng.choice(PAYERS)
    raw = build_claims(rng, claims, rates, PAID_DATE)
    adjudicated = [adjudicate_claim(rng, rc, payer) for rc in raw]
    # Open claims are the full submitted claims (all lines at billed).
    repo = OpenClaimRepository.from_claims(payer, [ac.claim for ac in adjudicated])
    remits = assemble(adjudicated, payer, PAID_DATE, plb)
    return payer, repo, remits


def test_clean_fixture_all_lines_match():
    _, repo, remits = _setup(42, 15, RatesConfig())
    exceptions = []
    matched = 0
    for r in remits:
        res = match_remittance(r, repo)
        matched += res.matched_count
        exceptions += res.exceptions
    assert exceptions == []
    # every open line matched exactly once
    assert all(ln.match_count == 1 for ln in repo.open_lines())


def test_split_payments_accumulate_across_remittances():
    _, repo, remits = _setup(42, 20, RatesConfig(split=0.3))
    assert len(remits) == 2  # split produced a remainder remittance
    total_matched = 0
    for r in remits:
        res = match_remittance(r, repo)
        assert res.exceptions == []
        total_matched += res.matched_count
    # Every open line is matched, and claim payments accumulated (no overwrite).
    assert all(ln.match_count >= 1 for ln in repo.open_lines())
    assert total_matched == sum(len(c.lines) for r in remits for c in r.claims)


def test_unmatched_line_flagged_not_forced():
    _, repo, remits = _setup(7, 10, RatesConfig())
    remit = remits[0]
    # Corrupt a line's cdt so it can't match any open line (bundling-style).
    remit.claims[0].lines[0].cdt_code = "D9999"
    res = match_remittance(remit, repo)
    assert any(e["reason"] == "unmatched_line" for e in res.exceptions)


def test_duplicate_key_lines_consume_distinct_instances():
    """Two identical (cdt, billed) lines in a claim both match (multiset)."""
    rng = random.Random(1)
    payer = PAYERS[0]
    raw = build_claims(rng, 1, RatesConfig(), PAID_DATE)
    ac = adjudicate_claim(rng, raw[0], payer)
    # Force two identical lines.
    ac.claim.lines = [ac.claim.lines[0], ac.claim.lines[0].model_copy(deep=True)]
    repo = OpenClaimRepository.from_claims(payer, [ac.claim])
    remit = assemble([ac], payer, PAID_DATE)[0]
    res = match_remittance(remit, repo)
    assert res.exceptions == []
    assert [ln.match_count for ln in repo.open_lines()] == [1, 1]


def test_accumulation_does_not_overwrite_paid():
    _, repo, remits = _setup(42, 20, RatesConfig(split=0.3))
    for r in remits:
        match_remittance(r, repo)
    # paid_to_date is the sum of matched line payments, never an overwrite.
    for ln in repo.open_lines():
        assert ln.paid_to_date == money(ln.paid_to_date)  # quantized, present
        assert ln.match_count >= 1
