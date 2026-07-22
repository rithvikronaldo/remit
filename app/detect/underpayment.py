"""Revenue integrity — the underpayment detector.

The arithmetic gate proves a line *balances*; reconciliation proves the deposit
*ties*. Neither can see a payer paying less than the rate it agreed to: the
shortfall hides inside a larger CO-45 write-off, the line still conserves and
the EFT still matches to the cent. The only witness is the practice's
contracted fee schedule (``contracts/fee_schedules.json``) — so this pass
compares each line's *stated* allowed against ``contracted_allowed()`` and
flags the gap as recoverable revenue.

Deliberately conservative (a shortfall claim sent back to a payer must be
defensible):
  - only primary paid claims (CLP status 1) are checked — denials are the
    appeal workflow, COB is a re-adjudication, reversals are negated;
  - only payers with a contract on file — no contract, no comparison (the
    default factor is a fallback for generation, not evidence);
  - a cent of rounding slack (``UNDERPAYMENT_TOLERANCE``) so noise never flags.

A finding does NOT mean the remittance is wrong — the money conserves and the
deposit ties. It means the practice is owed more than the payer sent. Flagged
lines are queued for a human with the contract math attached, and the batch is
held from auto-commit (fail-closed), same as any other flagged line.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from app.contracts import contracted_allowed, contracted_payers
from app.models import Remittance, money
from app.settle.engine import SettlementResult

ZERO = money(0)
STATUS_PRIMARY = "1"


def _tolerance() -> Decimal:
    return money(os.getenv("UNDERPAYMENT_TOLERANCE", "0.01"))


@dataclass(frozen=True)
class UnderpaymentFinding:
    claim_id: str
    line_index: int
    cdt_code: str
    billed: Decimal
    stated_allowed: Decimal          # what the EOB says the payer allowed
    contracted_allowed: Decimal      # what the contract entitles
    recoverable: Decimal             # contracted - stated: the money withheld


@dataclass
class UnderpaymentResult:
    payer: str
    contract_on_file: bool
    lines_checked: int = 0
    findings: list[UnderpaymentFinding] = field(default_factory=list)

    @property
    def total_recoverable(self) -> Decimal:
        return money(sum((f.recoverable for f in self.findings), Decimal("0")))


def detect_underpayments(
    remit: Remittance, *, tolerance: Optional[Decimal] = None,
) -> UnderpaymentResult:
    """Compare every eligible line's stated allowed against the contract."""
    tol = _tolerance() if tolerance is None else money(tolerance)
    result = UnderpaymentResult(
        payer=remit.payer,
        contract_on_file=remit.payer in contracted_payers(),
    )
    if not result.contract_on_file:
        return result

    for claim in remit.claims:
        if claim.clp_status_code != STATUS_PRIMARY:
            continue
        for idx, line in enumerate(claim.lines):
            if line.billed <= ZERO:
                continue
            result.lines_checked += 1
            contracted = contracted_allowed(remit.payer, line.cdt_code, line.billed)
            shortfall = money(contracted - line.allowed)
            if shortfall > tol:
                result.findings.append(UnderpaymentFinding(
                    claim_id=claim.claim_id, line_index=idx, cdt_code=line.cdt_code,
                    billed=line.billed, stated_allowed=line.allowed,
                    contracted_allowed=contracted, recoverable=shortfall,
                ))
    return result


def apply_findings(settlement: SettlementResult, result: UnderpaymentResult) -> SettlementResult:
    """Queue each underpaid line's settlement record with the contract math attached.

    Buckets are untouched — the line still conserves (the shortfall sits inside
    the CO write-off), so this is not an arithmetic correction. What changes is
    that the line no longer auto-commits: a human sees the recoverable amount.
    """
    by_line_id = {r.claim_line_id: r for r in settlement.records}
    for f in result.findings:
        rec = by_line_id.get(f"{settlement.remittance_id}:{f.claim_id}:{f.line_index}")
        if rec is None:
            continue
        exc = {
            "reason": "underpayment",
            "detail": (f"{f.claim_id} {f.cdt_code}: payer allowed {f.stated_allowed} "
                       f"vs contracted {f.contracted_allowed} — recoverable {f.recoverable}"),
            "contracted_allowed": str(f.contracted_allowed),
            "stated_allowed": str(f.stated_allowed),
            "recoverable": str(f.recoverable),
        }
        rec.status = "queued"
        rec.action = "review"
        rec.exceptions.append(exc)
        settlement.exceptions.append(exc)
    return settlement
