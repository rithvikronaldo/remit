# Remit — Build Spec: Phase 2

**The decision layer.** Companion to the Remit PRD (v3.0) and the Phase 0/1 spec. This is the core of the project: turning a coded adjustment into a correct, grounded, cited financial action. It consumes the canonical model (Phase 0) and the `retrieve()` function (Phase 1), and is scored against the golden set on its first run.

---

## What "deciding" means here

A claim line carries one or more **adjustments** — `(group_code, reason_code, amount)` triples. The decision layer runs **per adjustment**, not per line, and answers one question: *what action does this adjustment imply?*

```
action ∈ { contractual_writeoff, bill_patient, bill_secondary, appeal, review }
```

The line-level disposition and the eventual settlement (Phase 6) are derived from the per-adjustment decisions plus the amounts. Phase 2's only job is the action + its justification.

---

## The cascade — rules first, retrieval second, LLM last

Three tiers, in order. Most volume never reaches the model.

```
adjustment
   │
   ├─ Tier 1  RULES table?  ── hit ──► deterministic decision (confidence 1.0, no LLM)
   │            │ miss
   ├─ Tier 2  retrieve()  ── escalate signal ──► EXCEPTION (no grounding)
   │            │ chunks returned
   └─ Tier 3  LCEL chain (LLM, structured) ──► guardrails ──► decision | EXCEPTION
```

This is the design's whole safety argument: the LLM only ever sees denials, unknown codes, and genuinely ambiguous payer-specific cases — never the routine `CO-45` / `PR-2` bulk. Smaller blast radius, lower cost, lower latency, and every model decision is grounded and cited.

### Tier 1 — deterministic rules
A maintained map of the unambiguous pairs. Resolves the majority of real-world volume with zero model risk.

```python
# (group_code, reason_code) -> (action, billable_to_patient)
RULES = {
    ("CO", "45"): ("contractual_writeoff", False),   # exceeds fee schedule
    ("CO", "97"): ("contractual_writeoff", False),   # bundled/included
    ("PR", "1"):  ("bill_patient", True),            # deductible
    ("PR", "2"):  ("bill_patient", True),            # coinsurance
    ("PR", "3"):  ("bill_patient", True),            # copay
}
```
Everything else — denials (`CO/OA 197`, `29`, `50`…), `OA`/`PI` group codes, anything payer-specific — falls through to retrieval.

### Tier 2 — retrieval (the grounding gate)
Call `retrieve(group_code, reason_code, payer, cdt_code)` from Phase 1. If it returns the **escalate** signal (no canonical anchor and no rules above the similarity floor), stop here and raise an exception. **No grounding → no decision.** This is what makes "fail closed" real.

### Tier 3 — grounded LLM decision
Only reached with retrieved context in hand. The chain produces a structured decision justified *only* by that context.

---

## The decision schema (validated output)

```python
from typing import Literal
from pydantic import BaseModel, Field

class Decision(BaseModel):
    action: Literal["contractual_writeoff", "bill_patient",
                    "bill_secondary", "appeal", "review"]
    rationale: str = Field(..., description="One or two sentences, grounded in the cited context.")
    citations: list[str] = Field(..., description="knowledge_chunk ids that justify the action.")
    confidence: float = Field(..., ge=0.0, le=1.0)
```

`with_structured_output(Decision)` enforces the shape — free text is never parsed into an action.

---

## The LCEL chain

```python
from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate

llm = ChatAnthropic(model="claude-...", temperature=0).with_structured_output(Decision)

prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    ("human", HUMAN_PROMPT),
])

decision_chain = prompt | llm
```

`temperature=0` for reproducibility (the golden set must be stable across runs). Cache by `(adjustment, payer, cdt, corpus_version, prompt_version)` so re-evals are cheap.

### System prompt
```
You are a revenue-cycle adjudication assistant for a dental practice.
You decide the correct action for ONE claim adjustment.

Rules you must follow:
- Use ONLY the reference context provided in the message. Do not use outside knowledge.
- Every decision MUST cite the [id] of each context chunk that justifies it.
- A CO (contractual obligation) adjustment is NEVER billable to the patient.
- If the context does not justify a confident decision, choose action "review",
  cite what little is relevant, and return a low confidence.
- Calibrate confidence honestly: high only when the context directly supports the action.

Return the structured Decision.
```

### Human prompt
```
Adjustment: group {group_code}, reason code {reason_code}, amount {amount}
Payer: {payer}   Procedure: {cdt_code}

Reference context:
{context}        # each chunk rendered as:  [carc-197] CARC 197 — Precertification absent...
```

---

## The decide() orchestrator + guardrails

```python
THRESHOLD = 0.70

def decide_adjustment(adj, payer, cdt) -> DecisionResult:
    # Tier 1 — rules
    if (adj.group_code, adj.reason_code) in RULES:
        action, _ = RULES[(adj.group_code, adj.reason_code)]
        return DecisionResult(action=action, confidence=1.0, source="rules",
                              citations=[f"rule:{adj.group_code}-{adj.reason_code}"])

    # Tier 2 — retrieval / grounding gate
    r = retrieve(adj.group_code, adj.reason_code, payer, cdt)
    if r.escalate:
        return DecisionResult(escalate=True, reason="no_grounding")

    # Tier 3 — grounded LLM
    d = decision_chain.invoke({
        "group_code": adj.group_code, "reason_code": adj.reason_code,
        "amount": adj.amount, "payer": payer, "cdt_code": cdt,
        "context": render_chunks(r.chunks),
    })

    # Guardrails — any failure escalates instead of settling
    if not d.citations or not set(d.citations) <= set(r.citations):
        return DecisionResult(escalate=True, reason="uncited_or_fabricated_citation")
    if adj.group_code == "CO" and d.action == "bill_patient":
        return DecisionResult(escalate=True, reason="co_balance_bill_violation")
    if d.confidence < THRESHOLD:
        return DecisionResult(escalate=True, reason="low_confidence", decision=d)

    return DecisionResult(decision=d, source="rag")
```

Three guardrails matter most:
- **Citation containment** — the model's citations must be a subset of what retrieval actually returned. This kills fabricated citations: an id the model invented isn't in `r.citations`, so it escalates.
- **CO balance-bill block** — a hard domain rule the model can never override. If it ever recommends billing a patient for a contractual adjustment, that's an automatic escalation, not a settlement.
- **Confidence gate** — below threshold goes to a human with the model's draft attached, not into the settlement.

---

## Wiring to the golden set (first eval run)

The payoff of doing Phases 0/1 first: you can score Phase 2 the moment it runs.

```python
def run_eval(golden, version_tag):
    rows = []
    for case in golden.iter_adjustments():        # flattened to per-adjustment
        res = decide_adjustment(case.adj, case.payer, case.cdt)
        predicted = "escalate" if res.escalate else (res.decision.action if res.source=="rag"
                                                      else res.action)
        rows.append({
            "expected": case.expected_action,            # "escalate" for the should-review cases
            "predicted": predicted,
            "is_denial": case.is_denial,
            "source": getattr(res, "source", "escalate"),
            "citations_ok": _citations_ok(res, case),
        })
    return summarize(rows, version_tag)
```

### Metrics reported (against PRD §5.2 targets)
| Metric | How it's computed | Target |
|---|---|---|
| Decision accuracy | `predicted == expected` over all adjustments | ≥ 95% |
| Denial-action accuracy | same, over `is_denial` rows only | ≥ 95% |
| Citation coverage | RAG decisions with valid, contained citations ÷ RAG decisions | 100% |
| Escalation precision/recall | did it escalate exactly the should-review cases | high both |
| Tier mix | % resolved by rules vs. RAG vs. escalated | (report) |
| Latency | median per adjudicated line | < 60 s / remittance |

**Versioned & diffable.** Tag every run with `(model, prompt_version, corpus_version)`; persist results so a corpus edit that lifts denial accuracy but dents escalation precision is visible *before* you ship it. This loop — change a prompt or add a corpus doc, re-run, read the diff — is the thing that turns this from a demo into engineering.

---

## Definition of done (Phase 2)
- [ ] Tier 1 resolves the common `CO`/`PR` codes with no LLM call (verify in the tier-mix metric).
- [ ] Tier 2 escalates on genuinely ungrounded codes rather than calling the model.
- [ ] Tier 3 returns schema-valid, cited, confidence-scored decisions for denials/unknowns.
- [ ] All three guardrails fire correctly (unit-tested: fabricated citation, CO→bill_patient, sub-threshold).
- [ ] `run_eval` scores the golden set and meets the §5.2 targets.
- [ ] Runs are versioned, persisted, and diffable across prompt/corpus changes.

---

## What this unlocks
With Phase 2 green, the interesting part of the system is *done and measured*. Phases 3–4 (parsers/extraction) feed it real canonical lines, Phase 6 (settlement) consumes its decisions, and the eval harness (Phase 9) generalises this same `run_eval` to the full pipeline. Everything after this is plumbing around a decision engine you can already prove is correct.
