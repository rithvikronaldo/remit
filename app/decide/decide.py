"""The decide() orchestrator — the cascade + guardrails.

    adjustment
       ├─ Tier 0  claim-status override (reversal / COB) ─► deterministic decision
       ├─ Tier 1  RULES table ───────── hit ───────────► deterministic decision (conf 1.0)
       ├─ Tier 2  retrieve() ───── escalate signal ─────► EXCEPTION (no grounding)
       └─ Tier 3  LLM (grounded, structured) ─► guardrails ─► decision | EXCEPTION

The three guardrails are the whole safety argument; any failure escalates instead
of settling:
  1. Citation containment — the model's citations must be ⊆ what retrieval returned
     (kills fabricated citations).
  2. CO balance-bill block — a CO adjustment can never become bill_patient.
  3. Confidence gate — below threshold → human, with the draft attached.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Optional

from app.decide.cache import DecisionCache, make_key
from app.decide.chain import PROMPT_VERSION, DecisionChain, default_chain
from app.decide.rules import rule_lookup, status_override
from app.decide.schema import ACTIONS, Decision, DecisionResult
from app.kb.build import CORPUS_VERSION
from app.kb.retrieve import RetrievalResult, render_chunks, retrieve
from app.kb.store import VectorStore


def default_threshold() -> float:
    try:
        return float(os.getenv("DECISION_CONFIDENCE_THRESHOLD", "0.70"))
    except ValueError:
        return 0.70


# Process-level singletons (lazy).
_chain: Optional[DecisionChain] = None
_cache: Optional[DecisionCache] = None


def _get_chain() -> DecisionChain:
    global _chain
    if _chain is None:
        _chain = default_chain()
    return _chain


def _get_cache() -> DecisionCache:
    global _cache
    if _cache is None:
        _cache = DecisionCache()
    return _cache


def decide_adjustment(
    group_code: str,
    reason_code: str,
    amount: Decimal | float | str = 0,
    payer: Optional[str] = None,
    cdt_code: Optional[str] = None,
    claim_status: str = "1",
    *,
    store: Optional[VectorStore] = None,
    chain: Optional[DecisionChain] = None,
    cache: Optional[DecisionCache] = None,
    threshold: Optional[float] = None,
) -> DecisionResult:
    threshold = default_threshold() if threshold is None else threshold

    # --- Tier 0: claim-status overrides (reversal / COB) ---
    override = status_override(group_code, claim_status)
    if override:
        action, rule_id = override
        return DecisionResult(source="rules", action=action, confidence=1.0,
                              citations=[rule_id], rationale=f"Claim status {claim_status} → {action}.")

    # --- Tier 1: deterministic rules ---
    hit = rule_lookup(group_code, reason_code)
    if hit:
        action, _billable = hit
        return DecisionResult(source="rules", action=action, confidence=1.0,
                              citations=[f"rule:{group_code}-{reason_code}"],
                              rationale=f"Deterministic rule {group_code}-{reason_code} → {action}.")

    # --- Tier 2: retrieval / grounding gate ---
    r: RetrievalResult = retrieve(group_code, reason_code, payer, cdt_code, store=store)
    if r.escalate:
        return DecisionResult.escalated(r.reason or "no_grounding")

    # --- Tier 3: grounded LLM (cached) ---
    chain = chain or _get_chain()
    cache = cache if cache is not None else _get_cache()
    key = make_key(group_code, reason_code, payer, cdt_code, CORPUS_VERSION, PROMPT_VERSION)

    cached = cache.get(key)
    if cached is not None:
        d = Decision(**cached)
    else:
        d = chain.invoke({
            "group_code": group_code, "reason_code": reason_code, "amount": str(amount),
            "payer": payer or "any", "cdt_code": cdt_code or "unknown",
            "context": render_chunks(r.chunks), "_result": r,
        })
        cache.set(key, d.model_dump())

    return _apply_guardrails(d, r, group_code, threshold)


def _apply_guardrails(d: Decision, r: RetrievalResult, group_code: str, threshold: float) -> DecisionResult:
    # Guardrail 1 — citation containment (kills fabricated citations).
    if not d.citations or not set(d.citations) <= set(r.citations):
        return DecisionResult.escalated("uncited_or_fabricated_citation", decision=d)
    # Guardrail 2 — CO can never be billed to the patient (hard domain rule).
    if group_code == "CO" and d.action == "bill_patient":
        return DecisionResult.escalated("co_balance_bill_violation", decision=d)
    # Validity — action must be in the allowed set (schema already enforces, belt+braces).
    if d.action not in ACTIONS:
        return DecisionResult.escalated("invalid_action", decision=d)
    # Guardrail 3 — confidence gate.
    if d.confidence < threshold:
        return DecisionResult.escalated("low_confidence", decision=d)

    return DecisionResult(source="rag", action=d.action, confidence=d.confidence,
                          citations=list(d.citations), rationale=d.rationale, decision=d)
