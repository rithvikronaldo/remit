"""Phase 8 — exception queue + human-in-the-loop tests."""

from __future__ import annotations

import os

os.environ["REMIT_EMBEDDER"] = "hash"

import random
from datetime import date
from decimal import Decimal

import pytest

from app.decide.cache import DecisionCache
from app.decide.chain import MetadataStubChain
from app.exceptions import ExceptionQueue, ingest_results
from app.kb.build import build_store
from app.kb.embeddings import HashEmbedder
from app.match import OpenClaimRepository, match_remittance
from app.reconcile import reconcile
from app.settle import settle_remittance
from gen.adjudicator import adjudicate_claim
from gen.builder import assemble
from gen.catalog import PAYERS
from gen.claim_factory import RatesConfig, build_claims

PAID_DATE = date(2026, 6, 1)
STORE = build_store(embedder=HashEmbedder())


def test_add_sets_recommended_action_and_open_status():
    q = ExceptionQueue()
    item = q.add("no_grounding", claim_id="C1", cdt_code="D9999")
    assert item.status == "open"
    assert item.recommended_action == "review"
    assert q.open_items() == [item]


def test_accept_resolves_with_recommended_action_and_captures():
    q = ExceptionQueue()
    item = q.add("low_confidence", recommended_action="appeal",
                 evidence={"draft_action": "appeal", "confidence": 0.5})
    res = q.resolve(item.id, "accept", operator="alice")
    assert res.action == "appeal" and res.settled
    assert q.get(item.id).status == "resolved"
    assert q.open_items() == []
    assert q.resolutions[0]["resolved_action"] == "appeal"
    assert q.resolutions[0]["operator"] == "alice"


def test_override_requires_action_and_uses_operator_choice():
    q = ExceptionQueue()
    item = q.add("co_balance_bill_violation", recommended_action="review")
    with pytest.raises(ValueError):
        q.resolve(item.id, "override")          # no action given
    res = q.resolve(item.id, "override", action="contractual_writeoff", operator="bob")
    assert res.action == "contractual_writeoff"
    assert q.resolutions[0]["decision"] == "override"


def test_invalid_action_rejected():
    q = ExceptionQueue()
    item = q.add("unmatched_line")
    with pytest.raises(ValueError):
        q.resolve(item.id, "override", action="frobnicate")


def test_human_cannot_balance_bill_a_contractual_line():
    """Guardrail parity: a CO/PI (contractual) amount can never be billed to the
    patient — not even via a human override in the inbox."""
    q = ExceptionQueue()
    item = q.add("low_confidence", recommended_action="review",
                 evidence={"contractual_writeoff": "82.00"})
    with pytest.raises(ValueError):
        q.resolve(item.id, "override", action="bill_patient")
    # write-off is still allowed on the same line
    res = q.resolve(item.id, "override", action="contractual_writeoff")
    assert res.action == "contractual_writeoff"


def test_bill_patient_allowed_when_no_contractual_portion():
    q = ExceptionQueue()
    item = q.add("low_confidence", recommended_action="review",
                 evidence={"contractual_writeoff": "0.00", "patient_responsibility": "40.00"})
    res = q.resolve(item.id, "override", action="bill_patient")
    assert res.action == "bill_patient"


def test_resolve_is_idempotent():
    q = ExceptionQueue()
    item = q.add("reconciliation_break", recommended_action="review")
    first = q.resolve(item.id, "accept")
    again = q.resolve(item.id, "accept")
    assert first is again
    assert len(q.resolutions) == 1


def _pipeline(seed, claims, rates, plb=Decimal("0.00")):
    rng = random.Random(seed)
    payer = rng.choice(PAYERS)
    raw = build_claims(rng, claims, rates, PAID_DATE)
    adjudicated = [adjudicate_claim(rng, rc, payer) for rc in raw]
    repo = OpenClaimRepository.from_claims(payer, [ac.claim for ac in adjudicated])
    remits = assemble(adjudicated, payer, PAID_DATE, plb)
    cache = DecisionCache(path=None)
    q = ExceptionQueue()
    for remit in remits:
        m = match_remittance(remit, repo)
        s = settle_remittance(remit, store=STORE, chain=MetadataStubChain(), cache=cache)
        r = reconcile(remit, s)
        ingest_results(q, match_result=m, settlement=s, recon=r)
    return q


def test_overpayment_lands_in_queue_with_evidence():
    q = _pipeline(3, 12, RatesConfig(overpayment=0.5))
    items = q.open_items()
    assert items, "expected flagged overpayment lines in the queue"
    item = items[0]
    assert "billed" in item.evidence and "insurance_paid" in item.evidence
    assert item.claim_line_id is not None


def test_clean_pipeline_produces_no_exceptions():
    q = _pipeline(7, 15, RatesConfig())
    assert q.open_items() == []


def test_unmatched_line_routed_to_queue():
    rng = random.Random(7)
    payer = PAYERS[0]
    raw = build_claims(rng, 6, RatesConfig(), PAID_DATE)
    adjudicated = [adjudicate_claim(rng, rc, payer) for rc in raw]
    repo = OpenClaimRepository.from_claims(payer, [ac.claim for ac in adjudicated])
    remit = assemble(adjudicated, payer, PAID_DATE)[0]
    remit.claims[0].lines[0].cdt_code = "D9999"   # unmatchable
    m = match_remittance(remit, repo)
    q = ExceptionQueue()
    ingest_results(q, match_result=m)
    assert any(i.reason == "unmatched_line" for i in q.open_items())
