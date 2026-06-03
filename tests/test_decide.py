"""Phase 2 — decision layer tests. No API: rules/overrides are deterministic and
the LLM tier is exercised with mock chains. Forces the hash embedder for free,
offline, reproducible runs.
"""

from __future__ import annotations

import os

os.environ["REMIT_EMBEDDER"] = "hash"

from app.decide.cache import DecisionCache
from app.decide.decide import decide_adjustment
from app.decide.schema import Decision
from app.kb.build import build_store
from app.kb.embeddings import HashEmbedder

STORE = build_store(embedder=HashEmbedder())


def _mem_cache():
    return DecisionCache(path=None)


class FixedChain:
    """Returns a preset Decision; counts invocations."""
    name = "fixed"

    def __init__(self, decision: Decision):
        self.decision = decision
        self.calls = 0

    def invoke(self, inputs):
        self.calls += 1
        return self.decision


# --- Tier 0 / Tier 1: deterministic ---

def test_rules_tier_resolves_co_45_without_llm():
    chain = FixedChain(Decision(action="appeal", citations=["carc-45"], confidence=1.0, rationale="x"))
    res = decide_adjustment("CO", "45", 20, "DELTA DENTAL OF EXAMPLE", "D1110",
                            store=STORE, chain=chain, cache=_mem_cache())
    assert res.source == "rules"
    assert res.action == "contractual_writeoff"
    assert chain.calls == 0  # never reached the model


def test_pr_2_bills_patient_via_rules():
    res = decide_adjustment("PR", "2", 14, store=STORE, chain=FixedChain(
        Decision(action="review", citations=["x"], confidence=0.1, rationale="x")), cache=_mem_cache())
    assert res.source == "rules" and res.action == "bill_patient"


def test_cob_status_routes_pr_to_secondary():
    res = decide_adjustment("PR", "2", 14, claim_status="2", store=STORE, cache=_mem_cache())
    assert res.action == "bill_secondary" and res.source == "rules"


def test_reversal_status_routes_to_review():
    res = decide_adjustment("CO", "45", -20, claim_status="22", store=STORE, cache=_mem_cache())
    assert res.action == "review"


# --- Tier 2: grounding gate ---

def test_unknown_code_escalates_before_llm():
    chain = FixedChain(Decision(action="appeal", citations=["x"], confidence=1.0, rationale="x"))
    res = decide_adjustment("ZZ", "99999", 10, store=STORE, chain=chain, cache=_mem_cache())
    assert res.escalate and res.reason == "no_grounding"
    assert chain.calls == 0


# --- Tier 3 guardrails ---

def test_guardrail_fabricated_citation_escalates():
    chain = FixedChain(Decision(action="appeal", citations=["carc-DOESNOTEXIST"],
                                confidence=0.99, rationale="made up"))
    res = decide_adjustment("CO", "197", 100, "DELTA DENTAL OF EXAMPLE", "D2740",
                            store=STORE, chain=chain, cache=_mem_cache())
    assert res.escalate and res.reason == "uncited_or_fabricated_citation"


def test_guardrail_co_balance_bill_block():
    chain = FixedChain(Decision(action="bill_patient", citations=["carc-197"],
                                confidence=0.99, rationale="should be blocked"))
    res = decide_adjustment("CO", "197", 100, "DELTA DENTAL OF EXAMPLE", "D2740",
                            store=STORE, chain=chain, cache=_mem_cache())
    assert res.escalate and res.reason == "co_balance_bill_violation"


def test_guardrail_confidence_gate():
    chain = FixedChain(Decision(action="appeal", citations=["carc-197"],
                                confidence=0.5, rationale="unsure"))
    res = decide_adjustment("CO", "197", 100, "DELTA DENTAL OF EXAMPLE", "D2740",
                            store=STORE, chain=chain, cache=_mem_cache(), threshold=0.70)
    assert res.escalate and res.reason == "low_confidence"
    assert res.decision is not None  # draft attached for the human


def test_rag_success_returns_grounded_decision():
    chain = FixedChain(Decision(action="appeal", citations=["carc-197", "playbook-197-precert"],
                                confidence=0.95, rationale="precert appeal"))
    res = decide_adjustment("CO", "197", 100, "DELTA DENTAL OF EXAMPLE", "D2740",
                            store=STORE, chain=chain, cache=_mem_cache())
    assert res.source == "rag" and res.action == "appeal"
    assert set(res.citations) <= {"carc-197", "playbook-197-precert"}


def test_decision_cache_prevents_second_llm_call():
    chain = FixedChain(Decision(action="appeal", citations=["carc-197"], confidence=0.95, rationale="x"))
    cache = _mem_cache()
    a = decide_adjustment("CO", "197", 100, "DELTA DENTAL OF EXAMPLE", "D2740",
                          store=STORE, chain=chain, cache=cache)
    b = decide_adjustment("CO", "197", 100, "DELTA DENTAL OF EXAMPLE", "D2740",
                          store=STORE, chain=chain, cache=cache)
    assert a.action == b.action == "appeal"
    assert chain.calls == 1  # second call served from cache
