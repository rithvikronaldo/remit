"""Structured-output schema for the vision extraction path (Phase 4).

The vision LLM returns this strict shape with a per-line confidence; we never
believe free text. ``to_canonical`` projects it onto the shared ``Remittance``
model so the rest of the pipeline (gate, match, decide, settle) is identical
whether a line came from an 835 or a PDF.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models import Adjustment as CanonicalAdjustment
from app.models import Claim, ClaimLine, Remittance, money


class ExtractedAdjustment(BaseModel):
    group_code: str = Field(..., description="CO, PR, OA, or PI")
    reason_code: str = Field(..., description="CARC code, e.g. 45")
    amount: float


class ExtractedLine(BaseModel):
    cdt_code: str
    billed: float
    allowed: float
    paid: float
    patient_responsibility: float
    adjustments: list[ExtractedAdjustment] = []
    confidence: float = Field(..., ge=0.0, le=1.0,
                              description="Per-line confidence in the extracted money fields.")


class ExtractedClaim(BaseModel):
    claim_id: str
    patient_ref: str
    date_of_service: date
    clp_status_code: str
    lines: list[ExtractedLine] = []


class ExtractedRemittance(BaseModel):
    payer: str
    trn: str
    payment_method: str = "ACH"
    eft_amount: float
    paid_date: date
    payee: str = "BRIGHT SMILE DENTAL"
    plb_amount: float = 0.0
    claims: list[ExtractedClaim] = []

    def min_confidence(self) -> float:
        confs = [ln.confidence for c in self.claims for ln in c.lines]
        return min(confs) if confs else 1.0

    def to_canonical(self) -> Remittance:
        claims = []
        for c in self.claims:
            lines = []
            for ln in c.lines:
                lines.append(ClaimLine(
                    cdt_code=ln.cdt_code, billed=money(ln.billed), allowed=money(ln.allowed),
                    paid=money(ln.paid), patient_responsibility=money(ln.patient_responsibility),
                    adjustments=[CanonicalAdjustment(group_code=a.group_code, reason_code=a.reason_code,
                                                     amount=money(a.amount)) for a in ln.adjustments],
                    extraction_confidence=ln.confidence,
                ))
            claims.append(Claim(
                claim_id=c.claim_id, patient_ref=c.patient_ref, date_of_service=c.date_of_service,
                clp_status_code=c.clp_status_code,
                billed_total=money(sum((l.billed for l in lines), Decimal("0"))),
                paid_total=money(sum((l.paid for l in lines), Decimal("0"))),
                lines=lines,
            ))
        return Remittance(
            payer=self.payer, trn=self.trn, payment_method=self.payment_method,
            eft_amount=money(self.eft_amount), paid_date=self.paid_date, payee=self.payee,
            claims=claims, plb_amount=money(self.plb_amount),
        )
