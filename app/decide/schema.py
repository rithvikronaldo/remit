"""The decision schema (validated output of the decision layer).

The LLM returns a strict ``Decision`` via structured output — free text is never
parsed into a financial action. ``DecisionResult`` is the orchestrator's return:
either a decision (from rules or RAG) or an escalation with a reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import BaseModel, Field

Action = Literal["contractual_writeoff", "bill_patient", "bill_secondary", "appeal", "review"]
ACTIONS = ("contractual_writeoff", "bill_patient", "bill_secondary", "appeal", "review")


class Decision(BaseModel):
    """The LLM's structured decision for ONE adjustment."""
    action: Action
    rationale: str = Field(..., description="One or two sentences, grounded in the cited context.")
    citations: list[str] = Field(..., description="knowledge_chunk ids that justify the action.")
    confidence: float = Field(..., ge=0.0, le=1.0)


@dataclass
class DecisionResult:
    """Orchestrator output. ``source`` ∈ rules | rag | escalate."""
    source: str = "escalate"
    action: Optional[str] = None
    confidence: Optional[float] = None
    citations: list[str] = field(default_factory=list)
    rationale: Optional[str] = None
    escalate: bool = False
    reason: Optional[str] = None          # escalation reason (→ exception)
    decision: Optional[Decision] = None   # raw draft attached on low-confidence escalation

    @classmethod
    def escalated(cls, reason: str, decision: Optional[Decision] = None) -> "DecisionResult":
        return cls(source="escalate", escalate=True, reason=reason, decision=decision,
                   action=(decision.action if decision else None),
                   confidence=(decision.confidence if decision else None),
                   citations=(list(decision.citations) if decision else []))
