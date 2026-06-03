"""Canonical remittance model — the single in-memory schema every ingestion path
converges on, and the shape the golden set is labelled in.

This is shared by the generator (Phase 0), the 835 parser (Phase 3), the PDF
extractor (Phase 4), and the decision/settlement/reconciliation layers. Money is
always ``Decimal`` — never float — and quantized to cents.

Conservation invariants (PRD / BuildSpec Phase 0/1) are exposed via
``invariant_violations()`` rather than raised on construction, so that:
  - the generator can ``assert not r.invariant_violations()`` (invalid fixtures
    impossible by construction), while
  - the parser/extractor can construct a model and route any violation to an
    ``extraction_arithmetic_break`` exception instead of crashing.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from pydantic import BaseModel, field_validator

CENTS = Decimal("0.01")
GroupCode = Literal["CO", "PR", "OA", "PI"]
PaymentMethod = Literal["ACH", "CHK", "NON"]

# Group codes that reduce the allowed amount (contractual / payer-initiated).
WRITEOFF_GROUPS = ("CO", "PI")


def money(value) -> Decimal:
    """Coerce to a cent-quantized Decimal. Accepts str/int/float/Decimal."""
    return Decimal(str(value)).quantize(CENTS, rounding=ROUND_HALF_UP)


class Adjustment(BaseModel):
    group_code: GroupCode            # CO=contractual, PR=patient, OA/PI=other/payer
    reason_code: str                 # CARC, e.g. "45"
    amount: Decimal

    @field_validator("amount", mode="before")
    @classmethod
    def _q(cls, v):
        return money(v)


class ClaimLine(BaseModel):
    cdt_code: str                    # e.g. "D1110"
    billed: Decimal
    allowed: Decimal
    paid: Decimal
    adjustments: list[Adjustment] = []
    patient_responsibility: Decimal
    extraction_confidence: float = 1.0   # 1.0 for 835; model score for PDF

    @field_validator("billed", "allowed", "paid", "patient_responsibility", mode="before")
    @classmethod
    def _q(cls, v):
        return money(v)

    # --- derived sums ---
    def sum_adjustments(self) -> Decimal:
        return money(sum((a.amount for a in self.adjustments), Decimal("0")))

    def sum_group(self, *groups: str) -> Decimal:
        return money(sum((a.amount for a in self.adjustments if a.group_code in groups), Decimal("0")))

    @property
    def contractual_writeoff(self) -> Decimal:
        return self.sum_group("CO")

    @property
    def patient_from_adjustments(self) -> Decimal:
        return self.sum_group("PR")

    def invariant_violations(self) -> list[str]:
        v: list[str] = []
        # billed == paid + Σ(all adjustments)
        if self.billed != money(self.paid + self.sum_adjustments()):
            v.append(
                f"line {self.cdt_code}: billed {self.billed} != paid {self.paid} "
                f"+ Σadj {self.sum_adjustments()}"
            )
        # allowed == billed - Σ(CO/PI)
        if self.allowed != money(self.billed - self.sum_group(*WRITEOFF_GROUPS)):
            v.append(
                f"line {self.cdt_code}: allowed {self.allowed} != billed {self.billed} "
                f"- ΣCO/PI {self.sum_group(*WRITEOFF_GROUPS)}"
            )
        # patient_responsibility == Σ(PR)
        if self.patient_responsibility != self.patient_from_adjustments:
            v.append(
                f"line {self.cdt_code}: patient_responsibility {self.patient_responsibility} "
                f"!= ΣPR {self.patient_from_adjustments}"
            )
        return v


class Claim(BaseModel):
    claim_id: str
    patient_ref: str
    date_of_service: date
    clp_status_code: str             # 1=primary, 2=secondary, 4=denied, 22=reversal
    billed_total: Decimal
    paid_total: Decimal
    lines: list[ClaimLine] = []

    @field_validator("billed_total", "paid_total", mode="before")
    @classmethod
    def _q(cls, v):
        return money(v)

    def invariant_violations(self) -> list[str]:
        v: list[str] = []
        for line in self.lines:
            v.extend(line.invariant_violations())
        billed = money(sum((l.billed for l in self.lines), Decimal("0")))
        paid = money(sum((l.paid for l in self.lines), Decimal("0")))
        if self.billed_total != billed:
            v.append(f"claim {self.claim_id}: billed_total {self.billed_total} != Σ lines {billed}")
        if self.paid_total != paid:
            v.append(f"claim {self.claim_id}: paid_total {self.paid_total} != Σ lines {paid}")
        return v


class Remittance(BaseModel):
    payer: str
    trn: str                         # reassociation trace number
    payment_method: PaymentMethod
    eft_amount: Decimal
    paid_date: date
    payee: str = "BRIGHT SMILE DENTAL"
    claims: list[Claim] = []
    # Provider-level adjustments (PLB): affect EFT reconciliation outside any claim.
    plb_amount: Decimal = Decimal("0.00")

    @field_validator("eft_amount", "plb_amount", mode="before")
    @classmethod
    def _q(cls, v):
        return money(v)

    def sum_claim_paid(self) -> Decimal:
        return money(sum((c.paid_total for c in self.claims), Decimal("0")))

    def invariant_violations(self) -> list[str]:
        v: list[str] = []
        for claim in self.claims:
            v.extend(claim.invariant_violations())
        # Reassociation: eft_amount == Σ(claim.paid_total) + PLB
        expected_eft = money(self.sum_claim_paid() + self.plb_amount)
        if self.eft_amount != expected_eft:
            v.append(
                f"remittance: eft_amount {self.eft_amount} != Σ claim.paid_total "
                f"{self.sum_claim_paid()} + PLB {self.plb_amount}"
            )
        return v

    def assert_valid(self) -> "Remittance":
        """Generator entrypoint — invalid fixtures must be impossible by construction."""
        violations = self.invariant_violations()
        assert not violations, "Conservation invariants violated:\n  " + "\n  ".join(violations)
        return self
