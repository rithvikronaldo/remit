"""Phase 3 — 835 parser tests.

The headline test is the round-trip: generator → 835 → parser → canonical must
equal the generator's original Remittance, across every edge case. This proves
the X12 writer and the parser simultaneously.
"""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal

import pytest

from app.ingest import content_hash, detect_source_type
from app.parse import X12ParseError, parse_835
from gen.adjudicator import adjudicate_claim
from gen.builder import assemble
from gen.catalog import PAYERS
from gen.claim_factory import RatesConfig, build_claims
from gen.x12_writer import write_835

PAID_DATE = date(2026, 6, 1)


def _remittances(seed: int, claims: int, rates: RatesConfig, plb=Decimal("0.00")):
    rng = random.Random(seed)
    payer = rng.choice(PAYERS)
    raw = build_claims(rng, claims, rates, PAID_DATE)
    adjudicated = [adjudicate_claim(rng, rc, payer) for rc in raw]
    return assemble(adjudicated, payer, PAID_DATE, plb)


@pytest.mark.parametrize("seed", [1, 7, 42, 99])
def test_round_trip_all_edge_cases(seed):
    rates = RatesConfig(denial=0.1, reversal=0.05, cob=0.1, overpayment=0.05, split=0.1)
    for remit in _remittances(seed, 25, rates, plb=Decimal("-25.00")):
        text = write_835(remit)
        result = parse_835(text)
        assert result.exceptions == [], result.exceptions
        assert result.remittance == remit, f"round-trip mismatch (seed {seed}, trn {remit.trn})"


def test_round_trip_clean_fixture_no_exceptions():
    for remit in _remittances(3, 10, RatesConfig()):
        result = parse_835(write_835(remit))
        assert result.remittance == remit
        assert result.exceptions == []


def test_content_hash_is_stable_and_idempotency_key():
    remit = _remittances(42, 5, RatesConfig())[0]
    text = write_835(remit)
    assert parse_835(text).content_hash == parse_835(text).content_hash
    assert parse_835(text).content_hash == content_hash(text)


def test_malformed_envelope_rejected():
    with pytest.raises(X12ParseError):
        parse_835("not an x12 file at all~")
    with pytest.raises(X12ParseError):
        # ISA present but no GS/ST/SE/GE/IEA
        parse_835("ISA*00*x~")


def test_non_835_transaction_rejected():
    bad = (
        "ISA*00*          *00*          *ZZ*A              *ZZ*B              "
        "*260601*1200*^*00501*000000001*0*P*:~"
        "GS*HP*A*B*20260601*1200*1*X*005010X221A1~"
        "ST*837*0001~SE*2*0001~GE*1*1~IEA*1*000000001~"
    )
    with pytest.raises(X12ParseError):
        parse_835(bad)


def test_arithmetic_break_routed_not_raised():
    """A tampered paid amount breaks line conservation → exception, not a crash."""
    remit = _remittances(42, 3, RatesConfig())[0]
    text = write_835(remit)
    # Corrupt the first SVC paid amount so billed != paid + Σ adjustments.
    lines = text.split("~")
    for i, seg in enumerate(lines):
        if seg.strip().startswith("SVC*"):
            parts = seg.strip().split("*")
            parts[3] = "999999.99"
            lines[i] = "*".join(parts)
            break
    result = parse_835("~".join(lines))
    assert any(e["reason"] == "extraction_arithmetic_break" for e in result.exceptions)


def test_detect_source_type():
    assert detect_source_type("remit-001.835") == "835"
    assert detect_source_type("eob.pdf") == "pdf"
    assert detect_source_type("x.bin", b"%PDF-1.7") == "pdf"
    assert detect_source_type("x.bin", "ISA*00*...") == "835"
