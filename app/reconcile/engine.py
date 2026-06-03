"""Phase 7 — reconciliation. Prove the remittance ties to the money received.

Two checks:
  1. Line conservation re-asserted after settlement: P+W+R+S+A == billed, per
     settled/appealed line (queued lines are already held as exceptions).
  2. Reassociation: Σ insurance_paid across lines + PLB == EFT amount (the BPR
     total), matched via the TRN trace number.

Only ``insurance_paid`` is real money out, so only it (plus provider-level PLB)
reconciles to the EFT — write-offs, patient/secondary responsibility, and
appealed-open balances do not move money.

A delta of any cent is a hard fail → ``reconciliation_break``; the remittance is
**held, not partially committed**. ``commit_if_reconciled`` is the pre-commit gate
on the settlement transaction so a break never leaves half-settled claims.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.models import Remittance, money
from app.settle.engine import SettlementResult, SettlementStore

ZERO = money(0)


@dataclass
class ReconciliationResult:
    remittance_id: str               # TRN trace number
    eft_amount: Decimal
    sum_insurance_paid: Decimal
    plb_amount: Decimal
    delta: Decimal
    tied: bool
    held: bool
    exceptions: list[dict] = field(default_factory=list)


def reconcile(remit: Remittance, settlement: SettlementResult) -> ReconciliationResult:
    exceptions: list[dict] = []

    # Check 1 — line conservation for lines that claim to be settled/appealed.
    for rec in settlement.records:
        if rec.status in ("settled", "appealed") and not rec.fully_accounted():
            exceptions.append({
                "reason": "reconciliation_break",
                "detail": f"line {rec.claim_line_id} does not conserve (status {rec.status})",
            })

    # Check 2 — reassociation against the EFT via TRN.
    sum_paid = money(sum((r.insurance_paid for r in settlement.records), Decimal("0")))
    expected_eft = money(sum_paid + remit.plb_amount)
    delta = money(remit.eft_amount - expected_eft)
    if delta != ZERO:
        exceptions.append({
            "reason": "reconciliation_break",
            "detail": (f"EFT {remit.eft_amount} != Σ insurance_paid {sum_paid} "
                       f"+ PLB {remit.plb_amount} (delta {delta})"),
        })

    tied = not exceptions
    # Held if it doesn't tie, or if settlement already raised exceptions (flagged lines).
    held = (not tied) or bool(settlement.exceptions)
    return ReconciliationResult(
        remittance_id=remit.trn, eft_amount=remit.eft_amount, sum_insurance_paid=sum_paid,
        plb_amount=remit.plb_amount, delta=delta, tied=tied, held=held, exceptions=exceptions,
    )


def commit_if_reconciled(
    store: SettlementStore, remit: Remittance, settlement: SettlementResult,
) -> tuple[ReconciliationResult, int]:
    """Pre-commit gate: reconcile, and only write to the store if the remittance
    ties AND has no held lines. Returns (reconciliation, records_written)."""
    recon = reconcile(remit, settlement)
    if recon.held:
        return recon, 0
    return recon, store.commit(settlement)
