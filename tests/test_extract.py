"""Phase 4 — PDF EOB extraction tests.

The text-layer path is deterministic and free, so we extract real generated PDFs
and assert they reconstruct the original canonical model (≥98% money-field
accuracy → exact here). The vision path's gate (arithmetic + confidence) is
exercised with a stub extractor, no API.
"""

from __future__ import annotations

import os
import random
import tempfile
from datetime import date
from decimal import Decimal

import pytest

from app.extract import extract_pdf, has_text_layer
from app.extract.schema import (ExtractedClaim, ExtractedLine, ExtractedRemittance)
from gen.adjudicator import adjudicate_claim
from gen.builder import assemble
from gen.catalog import PAYERS
from gen.claim_factory import RatesConfig, build_claims
from gen.eob_pdf import render_eob_pdf

PAID_DATE = date(2026, 6, 1)


def _render_remits(seed, claims, rates, plb=Decimal("0.00")):
    rng = random.Random(seed)
    payer = rng.choice(PAYERS)
    raw = build_claims(rng, claims, rates, PAID_DATE)
    adj = [adjudicate_claim(rng, rc, payer) for rc in raw]
    remits = assemble(adj, payer, PAID_DATE, plb)
    d = tempfile.mkdtemp()
    paths = []
    for i, r in enumerate(remits, 1):
        p = os.path.join(d, f"eob-{i}.pdf")
        render_eob_pdf(r, p)
        paths.append(p)
    return list(zip(paths, remits))


@pytest.mark.parametrize("seed", [1, 42, 99])
def test_text_layer_reconstructs_original(seed):
    rates = RatesConfig(denial=0.1, reversal=0.05, cob=0.1, overpayment=0.05, split=0.1)
    for path, remit in _render_remits(seed, 20, rates, plb=Decimal("-25.00")):
        res = extract_pdf(path)
        assert res.source == "text_layer"
        assert res.exceptions == [], res.exceptions
        assert res.remittance == remit


def test_money_field_accuracy_is_exact():
    """Field extraction accuracy on key money fields (billed/allowed/paid/PR)."""
    total = correct = 0
    for path, remit in _render_remits(7, 15, RatesConfig(denial=0.1, cob=0.1)):
        ex = extract_pdf(path).remittance
        for ec, oc in zip(ex.claims, remit.claims):
            for el, ol in zip(ec.lines, oc.lines):
                for field in ("billed", "allowed", "paid", "patient_responsibility"):
                    total += 1
                    correct += getattr(el, field) == getattr(ol, field)
    assert total > 0
    assert correct / total >= 0.98


def test_has_text_layer_true_for_generated_pdfs():
    path, _ = _render_remits(1, 3, RatesConfig())[0]
    assert has_text_layer(path)


# --- vision path gate (stub extractor, no API) ---

class StubExtractor:
    name = "stub"
    def __init__(self, extracted): self.extracted = extracted
    def extract(self, pdf_path): return self.extracted


def _good_extracted():
    return ExtractedRemittance(
        payer="CIGNA DENTAL", trn="EFT1", eft_amount=80.0, paid_date=PAID_DATE,
        claims=[ExtractedClaim(claim_id="C1", patient_ref="M1", date_of_service=PAID_DATE,
            clp_status_code="1", lines=[ExtractedLine(
                cdt_code="D1110", billed=120.0, allowed=100.0, paid=80.0,
                patient_responsibility=20.0, confidence=0.99,
                adjustments=[{"group_code": "CO", "reason_code": "45", "amount": 20.0},
                             {"group_code": "PR", "reason_code": "2", "amount": 20.0}])])])


def test_vision_good_extraction_passes_gate():
    path, _ = _render_remits(1, 1, RatesConfig())[0]
    res = extract_pdf(path, extractor=StubExtractor(_good_extracted()), force_vision=True)
    assert res.source == "vision"
    assert res.exceptions == []
    assert res.remittance.claims[0].lines[0].paid == Decimal("80.00")


def test_vision_arithmetic_break_routed():
    bad = _good_extracted()
    bad.claims[0].lines[0].paid = 999.0   # billed != paid + Σ adj
    path, _ = _render_remits(1, 1, RatesConfig())[0]
    res = extract_pdf(path, extractor=StubExtractor(bad), force_vision=True)
    assert any(e["reason"] == "extraction_arithmetic_break" for e in res.exceptions)


def test_vision_low_confidence_flagged():
    low = _good_extracted()
    low.claims[0].lines[0].confidence = 0.40
    path, _ = _render_remits(1, 1, RatesConfig())[0]
    res = extract_pdf(path, extractor=StubExtractor(low), force_vision=True, floor=0.90)
    assert any(e["reason"] == "low_confidence_extraction" for e in res.exceptions)
