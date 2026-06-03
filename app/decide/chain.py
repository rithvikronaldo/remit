"""The decision chain — Tier 3.

Two interchangeable implementations behind ``invoke(inputs) -> Decision``:
  - ``build_decision_chain()`` — the real grounded LCEL chain: Claude Haiku 4.5,
    temperature 0, structured output. Cheap tier; only ever sees denials/unknowns.
  - ``MetadataStubChain`` — a free, deterministic, offline baseline that reads the
    recommended action from the retrieved chunks' metadata. Lets the full eval run
    with NO API key (and proves the harness + guardrails). Swapped for the real
    chain automatically when ANTHROPIC_API_KEY is set.

``inputs`` carries the prompt variables plus ``_result`` (the RetrievalResult);
the real chain ignores ``_result`` and the stub uses it.
"""

from __future__ import annotations

import os
from typing import Protocol

from app.decide.schema import Decision

MODEL = os.getenv("DECISION_MODEL", "claude-haiku-4-5")
PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """You are a revenue-cycle adjudication assistant for a dental practice.
You decide the correct action for ONE claim adjustment.

Rules you must follow:
- Use ONLY the reference context provided in the message. Do not use outside knowledge.
- Every decision MUST cite the [id] of each context chunk that justifies it.
- A CO (contractual obligation) adjustment is NEVER billable to the patient.
- If the context does not justify a confident decision, choose action "review",
  cite what little is relevant, and return a low confidence.
- Calibrate confidence honestly: high only when the context directly supports the action.

Return the structured Decision."""

HUMAN_PROMPT = """Adjustment: group {group_code}, reason code {reason_code}, amount {amount}
Payer: {payer}   Procedure: {cdt_code}

Reference context:
{context}"""


class DecisionChain(Protocol):
    def invoke(self, inputs: dict) -> Decision: ...


class MetadataStubChain:
    """Deterministic, free baseline. Derives the action from retrieved metadata:
    a denial playbook's recommended_action if present, else the CARC's
    default_action. 'review' (ambiguous) gets a sub-threshold confidence so the
    confidence gate escalates it — matching the should-review golden cases."""

    name = "metadata-stub"

    def invoke(self, inputs: dict) -> Decision:
        r = inputs["_result"]
        reason = inputs["reason_code"]
        action, cite = None, []

        for c in r.chunks:
            if c.chunk_type == "playbook":
                action = c.metadata.get("recommended_action")
                cite = [c.id]
                break
        if action is None:
            for c in r.chunks:
                if c.chunk_type == "carc" and c.code == reason:
                    action = c.metadata.get("default_action")
                    cite = [c.id]
                    break
        if action is None:
            action = "review"
            cite = [r.chunks[0].id] if r.chunks else []

        confidence = 0.9 if action != "review" else 0.5
        return Decision(action=action, citations=cite, confidence=confidence,
                        rationale=f"Stub baseline: '{action}' from retrieved {cite}.")


def build_decision_chain(model: str = MODEL):
    """The real grounded LCEL chain. Lazy imports so the module loads without
    langchain installed (tests use the stub)."""
    from langchain_anthropic import ChatAnthropic
    from langchain_core.prompts import ChatPromptTemplate

    llm = ChatAnthropic(model=model, temperature=0).with_structured_output(Decision)
    prompt = ChatPromptTemplate.from_messages([("system", SYSTEM_PROMPT), ("human", HUMAN_PROMPT)])
    lcel = prompt | llm

    class _RealChain:
        name = model

        def invoke(self, inputs: dict) -> Decision:
            vars_ = {k: v for k, v in inputs.items() if not k.startswith("_")}
            return lcel.invoke(vars_)

    return _RealChain()


def default_chain() -> DecisionChain:
    """Real chain when an API key is configured; the free stub otherwise."""
    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            return build_decision_chain()
        except Exception:
            pass
    return MetadataStubChain()
