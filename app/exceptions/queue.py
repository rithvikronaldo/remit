"""Phase 8 — exception queue + human-in-the-loop.

A single triage surface for everything the pipeline refused to auto-process.
Every fail-closed path lands here with its evidence and a recommended action:

    extraction_arithmetic_break   (parse / extract)
    unmatched_line                (match)
    no_grounding                  (decide — Tier 2)
    low_confidence                (decide — confidence gate)
    co_balance_bill_violation     (decide — guardrail)
    uncited_or_fabricated_citation(decide — guardrail)
    reconciliation_break          (reconcile)
    review_required               (settle — flagged line, e.g. overpayment)

Resolution: accept the recommendation (which then settles) or override it
(operator picks the action → settles). Every resolution is captured to seed the
eval/golden set — the start of an active-learning loop.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional

# Default recommended action per reason when none is supplied by the stage.
DEFAULT_RECOMMENDED = {
    "extraction_arithmetic_break": "review",
    "low_confidence_extraction": "review",
    "unmatched_line": "review",
    "no_grounding": "review",
    "low_confidence": "review",
    "co_balance_bill_violation": "review",
    "uncited_or_fabricated_citation": "review",
    "reconciliation_break": "review",
    "review_required": "review",
    "settlement_not_balanced": "review",
}

VALID_ACTIONS = {"contractual_writeoff", "bill_patient", "bill_secondary", "appeal", "review"}


@dataclass
class Resolution:
    item_id: str
    decision: str            # "accept" | "override"
    action: str
    operator: str
    settled: bool = True


@dataclass
class ExceptionItem:
    id: str
    reason: str
    recommended_action: str
    claim_id: Optional[str] = None
    claim_line_id: Optional[str] = None
    cdt_code: Optional[str] = None
    confidence: Optional[float] = None
    evidence: dict = field(default_factory=dict)
    status: str = "open"     # open | resolved
    resolution: Optional[Resolution] = None


class ExceptionQueue:
    def __init__(self) -> None:
        self._items: dict[str, ExceptionItem] = {}
        self.resolutions: list[dict] = []      # the active-learning / eval seed

    # --- intake ---
    def add(self, reason: str, *, claim_id=None, claim_line_id=None, cdt_code=None,
            confidence=None, evidence=None, recommended_action=None) -> ExceptionItem:
        item = ExceptionItem(
            id=str(uuid.uuid4()),
            reason=reason,
            recommended_action=recommended_action or DEFAULT_RECOMMENDED.get(reason, "review"),
            claim_id=claim_id, claim_line_id=claim_line_id, cdt_code=cdt_code,
            confidence=confidence, evidence=evidence or {},
        )
        self._items[item.id] = item
        return item

    # --- triage views ---
    def list(self, status: Optional[str] = None) -> list[ExceptionItem]:
        items = list(self._items.values())
        return [i for i in items if i.status == status] if status else items

    def open_items(self) -> list[ExceptionItem]:
        return self.list("open")

    def get(self, item_id: str) -> ExceptionItem:
        return self._items[item_id]

    # --- resolution ---
    def resolve(self, item_id: str, decision: str, *, action: Optional[str] = None,
                operator: str = "system") -> Resolution:
        item = self._items[item_id]
        if item.status == "resolved":
            return item.resolution
        if decision == "accept":
            chosen = item.recommended_action
        elif decision == "override":
            if action is None:
                raise ValueError("override requires an explicit action")
            chosen = action
        else:
            raise ValueError(f"unknown decision {decision!r}")
        if chosen not in VALID_ACTIONS:
            raise ValueError(f"invalid action {chosen!r}")
        # Guardrail parity with the engine: a contractual (CO/PI) amount can never be
        # balance-billed to the patient — not even via a human override.
        try:
            contractual = float(item.evidence.get("contractual_writeoff") or 0)
        except (TypeError, ValueError):
            contractual = 0.0
        if chosen == "bill_patient" and contractual > 0:
            raise ValueError("cannot balance-bill a contractual adjustment to the patient")

        res = Resolution(item_id=item_id, decision=decision, action=chosen, operator=operator)
        item.status = "resolved"
        item.resolution = res
        # Capture for eval / corpus enrichment (the seed of active learning).
        self.resolutions.append({
            "reason": item.reason, "claim_id": item.claim_id, "cdt_code": item.cdt_code,
            "evidence": item.evidence, "resolved_action": chosen,
            "decision": decision, "operator": operator,
        })
        return res


def ingest_results(queue: ExceptionQueue, *, parse_result=None, match_result=None,
                   settlement=None, recon=None) -> ExceptionQueue:
    """Route every stage's exceptions into the queue with evidence attached."""
    if parse_result is not None:
        for e in getattr(parse_result, "exceptions", []):
            queue.add(e["reason"], evidence={"detail": e.get("detail")})

    if match_result is not None:
        for e in getattr(match_result, "exceptions", []):
            queue.add(e["reason"], evidence={"detail": e.get("detail")})

    if settlement is not None:
        for rec in getattr(settlement, "records", []):
            if rec.status != "queued":
                continue
            reason = rec.exceptions[0]["reason"] if rec.exceptions else "review_required"
            queue.add(
                reason, claim_id=rec.claim_id, claim_line_id=rec.claim_line_id,
                cdt_code=rec.cdt_code, recommended_action=rec.action,
                evidence={
                    "billed": str(rec.billed), "insurance_paid": str(rec.insurance_paid),
                    "contractual_writeoff": str(rec.contractual_writeoff),
                    "patient_responsibility": str(rec.patient_responsibility),
                    "secondary_responsibility": str(rec.secondary_responsibility),
                    "appealed_open": str(rec.appealed_open),
                    "other_adjustments": str(rec.billed - rec.insurance_paid - rec.contractual_writeoff
                                             - rec.patient_responsibility - rec.secondary_responsibility - rec.appealed_open),
                    "line_exceptions": rec.exceptions,
                },
            )

    if recon is not None:
        for e in getattr(recon, "exceptions", []):
            queue.add(e["reason"], claim_line_id=getattr(recon, "remittance_id", None),
                      evidence={"detail": e.get("detail"), "delta": str(getattr(recon, "delta", ""))})

    return queue
