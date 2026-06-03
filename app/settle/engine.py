"""Phase 6 — settlement recording.

Turns matched lines + their per-adjustment decisions (Phase 2) into one settlement
record per line. The check is arithmetic, not bookkeeping:

    insurance_paid(P) + contractual_writeoff(W) + patient_responsibility(R)
        + secondary_responsibility(S) + appealed_open(A)  ==  billed(B)

Each adjustment's *amount* is routed to a bucket by its decided *action*:
  contractual_writeoff→W, bill_patient→R, bill_secondary→S, appeal→A (stays open
  against insurance), review/escalate→the line is queued for a human.

Claim status drives structural cases: status 22 (reversal) records a reversing
settlement (negated buckets, settled), not a re-decision; status 2 (COB) routes
PR to the secondary (the decision layer already returns bill_secondary).

Records are append-only/immutable (corrections are new records), atomic per
remittance (all lines or none), and idempotent per (remittance_id, claim_line_id).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from app.decide.cache import DecisionCache
from app.decide.chain import DecisionChain
from app.decide.decide import decide_adjustment
from app.models import Claim, ClaimLine, Remittance, money
from app.kb.store import VectorStore

ZERO = money(0)
STATUS_REVERSAL = "22"


@dataclass
class LineSettlement:
    remittance_id: str
    claim_line_id: str
    claim_id: str
    cdt_code: str
    billed: Decimal
    insurance_paid: Decimal
    contractual_writeoff: Decimal
    patient_responsibility: Decimal
    secondary_responsibility: Decimal
    appealed_open: Decimal
    action: str
    status: str                       # settled | appealed | queued
    exceptions: list[dict] = field(default_factory=list)

    def fully_accounted(self) -> bool:
        total = (self.insurance_paid + self.contractual_writeoff + self.patient_responsibility
                 + self.secondary_responsibility + self.appealed_open)
        return money(total) == money(self.billed)


@dataclass
class SettlementResult:
    remittance_id: str
    records: list[LineSettlement] = field(default_factory=list)
    exceptions: list[dict] = field(default_factory=list)

    @property
    def committed(self) -> bool:
        """Atomic: the remittance commits only if every line is fully accounted."""
        return all(r.fully_accounted() for r in self.records)


def _line_id(remittance_id: str, claim_id: str, idx: int) -> str:
    return f"{remittance_id}:{claim_id}:{idx}"


def _summary_action(buckets: dict, status: str) -> str:
    if status == "queued":
        return "review"
    if status == "appealed":
        return "appeal"
    if buckets["S"] > ZERO and buckets["W"] == ZERO and buckets["R"] == ZERO:
        return "bill_secondary"
    if buckets["R"] == ZERO and buckets["S"] == ZERO and buckets["A"] == ZERO:
        return "contractual_writeoff"
    return "settled"


def settle_line(
    line: ClaimLine, claim: Claim, payer: str, remittance_id: str, idx: int,
    *, store: Optional[VectorStore] = None, chain: Optional[DecisionChain] = None,
    cache: Optional[DecisionCache] = None, threshold: Optional[float] = None,
) -> LineSettlement:
    status_code = claim.clp_status_code
    buckets = {"W": ZERO, "R": ZERO, "S": ZERO, "A": ZERO}
    exceptions: list[dict] = []

    for adj in line.adjustments:
        if status_code == STATUS_REVERSAL:
            # Reversing entry: bucket by group, ignore the per-adjustment 'review'.
            if adj.group_code in ("CO", "PI"):
                buckets["W"] = money(buckets["W"] + adj.amount)
            elif adj.group_code == "PR":
                buckets["R"] = money(buckets["R"] + adj.amount)
            continue

        res = decide_adjustment(adj.group_code, adj.reason_code, adj.amount, payer,
                                line.cdt_code, status_code,
                                store=store, chain=chain, cache=cache, threshold=threshold)
        if res.escalate or res.action == "review":
            exceptions.append({
                "reason": res.reason or "review_required",
                "detail": f"{claim.claim_id} {line.cdt_code} {adj.group_code}-{adj.reason_code}",
            })
            # Place by group so arithmetic still balances where it can; status→queued.
            if adj.group_code in ("CO", "PI"):
                buckets["W"] = money(buckets["W"] + adj.amount)
            elif adj.group_code == "PR":
                buckets["R"] = money(buckets["R"] + adj.amount)
            continue

        action = res.action
        if action == "contractual_writeoff":
            buckets["W"] = money(buckets["W"] + adj.amount)
        elif action == "bill_patient":
            buckets["R"] = money(buckets["R"] + adj.amount)
        elif action == "bill_secondary":
            buckets["S"] = money(buckets["S"] + adj.amount)
        elif action == "appeal":
            buckets["A"] = money(buckets["A"] + adj.amount)

    if exceptions:
        status = "queued"
    elif buckets["A"] > ZERO:
        status = "appealed"
    else:
        status = "settled"

    return LineSettlement(
        remittance_id=remittance_id, claim_line_id=_line_id(remittance_id, claim.claim_id, idx),
        claim_id=claim.claim_id, cdt_code=line.cdt_code, billed=line.billed,
        insurance_paid=line.paid, contractual_writeoff=buckets["W"],
        patient_responsibility=buckets["R"], secondary_responsibility=buckets["S"],
        appealed_open=buckets["A"], action=_summary_action(buckets, status),
        status=status, exceptions=exceptions,
    )


def settle_remittance(
    remit: Remittance, *, store: Optional[VectorStore] = None,
    chain: Optional[DecisionChain] = None, cache: Optional[DecisionCache] = None,
    threshold: Optional[float] = None,
) -> SettlementResult:
    result = SettlementResult(remittance_id=remit.trn)
    for claim in remit.claims:
        for idx, line in enumerate(claim.lines):
            rec = settle_line(line, claim, remit.payer, remit.trn, idx,
                              store=store, chain=chain, cache=cache, threshold=threshold)
            result.records.append(rec)
            result.exceptions.extend(rec.exceptions)
            if not rec.fully_accounted():
                result.exceptions.append({
                    "reason": "settlement_not_balanced",
                    "detail": f"{rec.claim_line_id}: P+W+R+S+A != billed {rec.billed}",
                })
    return result


class SettlementStore:
    """Append-only, idempotent per (remittance_id, claim_line_id)."""

    def __init__(self) -> None:
        self._records: list[LineSettlement] = []
        self._seen: set[tuple[str, str]] = set()

    def commit(self, result: SettlementResult) -> int:
        """Append records for a committed remittance; idempotent on replay.
        Returns the number of newly-written records."""
        if not result.committed:
            return 0
        written = 0
        for rec in result.records:
            key = (rec.remittance_id, rec.claim_line_id)
            if key in self._seen:
                continue
            self._seen.add(key)
            self._records.append(rec)
            written += 1
        return written

    def __len__(self) -> int:
        return len(self._records)
