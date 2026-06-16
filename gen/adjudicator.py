"""The adjudicator — the heart of Phase 0.

For each line: look up ``allowed`` from the payer fee schedule, derive the
contractual write-off ``CO-45 = billed - allowed``, split the remainder into
``paid`` and patient responsibility (PR-1 deductible / PR-2 coinsurance), then
apply the claim's edge-case role. Every adjudicated claim satisfies the model
invariants by construction (the generator asserts this downstream).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal

from app.models import Adjustment, Claim, ClaimLine, money
from gen.catalog import (
    CO_FEE_SCHEDULE_CARC,
    DENIAL_CARCS,
    OVERPAYMENT_CARC,
)
from gen.claim_factory import (
    RawClaim,
    ROLE_COB,
    ROLE_DENIAL,
    ROLE_OVERPAYMENT,
    ROLE_REVERSAL,
    ROLE_UNDERPAYMENT,
)
from gen.fee_schedule import allowed_amount

ZERO = money(0)

# CLP status code per role.
_STATUS = {
    "normal": "1",
    "split": "1",
    "overpayment": "1",
    ROLE_UNDERPAYMENT: "1",   # the claim is "paid" — just paid less than the contract
    ROLE_COB: "2",
    ROLE_DENIAL: "4",
    ROLE_REVERSAL: "22",
}


@dataclass
class AdjudicatedClaim:
    claim: Claim
    role: str
    payer: str
    # Per claim_line index → notes for the golden set (denial CARC, overpayment, …).
    notes: dict[int, dict]


def _normal_line(rng: random.Random, payer: str, cdt: str, billed: Decimal) -> ClaimLine:
    allowed = allowed_amount(payer, cdt, billed)
    co = money(billed - allowed)

    deductible = money(rng.choice([0, 0, 0, 25, 50]))
    deductible = min(deductible, allowed)
    coins_rate = rng.choice([Decimal("0.0"), Decimal("0.2"), Decimal("0.2"), Decimal("0.3")])
    coinsurance = money((allowed - deductible) * coins_rate)
    pr_total = money(deductible + coinsurance)
    if pr_total > allowed:
        pr_total = allowed
        coinsurance = money(pr_total - deductible)
    paid = money(allowed - pr_total)

    adjustments: list[Adjustment] = []
    if co > ZERO:
        adjustments.append(Adjustment(group_code="CO", reason_code=CO_FEE_SCHEDULE_CARC, amount=co))
    if deductible > ZERO:
        adjustments.append(Adjustment(group_code="PR", reason_code="1", amount=deductible))
    if coinsurance > ZERO:
        adjustments.append(Adjustment(group_code="PR", reason_code="2", amount=coinsurance))

    return ClaimLine(
        cdt_code=cdt,
        billed=billed,
        allowed=allowed,
        paid=paid,
        adjustments=adjustments,
        patient_responsibility=pr_total,
    )


def _denial_line(rng: random.Random, cdt: str, billed: Decimal) -> tuple[ClaimLine, dict]:
    carc = rng.choice(list(DENIAL_CARCS.keys()))
    info = DENIAL_CARCS[carc]
    line = ClaimLine(
        cdt_code=cdt,
        billed=billed,
        allowed=ZERO,
        paid=ZERO,
        adjustments=[Adjustment(group_code=info["group"], reason_code=carc, amount=billed)],
        patient_responsibility=ZERO,
    )
    return line, {"denial_carc": carc, "expected_action": info["action"]}


def _overpayment_line(rng: random.Random, payer: str, cdt: str, billed: Decimal) -> tuple[ClaimLine, dict]:
    base = _normal_line(rng, payer, cdt, billed)
    delta = money(base.patient_responsibility + money(rng.randint(5, 20)))
    new_paid = money(base.paid + delta)
    adjustments = list(base.adjustments)
    # OA-23: a negative adjustment putting money back — keeps billed == paid + Σadj.
    adjustments.append(Adjustment(group_code="OA", reason_code=OVERPAYMENT_CARC, amount=money(-delta)))
    line = ClaimLine(
        cdt_code=cdt,
        billed=billed,
        allowed=base.allowed,
        paid=new_paid,
        adjustments=adjustments,
        patient_responsibility=base.patient_responsibility,
    )
    return line, {"overpayment": True, "expected_action": "review"}


def _underpayment_line(rng: random.Random, payer: str, cdt: str, billed: Decimal) -> tuple[ClaimLine, dict]:
    """The payer allows *less* than the contracted rate and buries the shortfall in a
    larger CO-45 write-off. The line still balances (billed == paid + Σadj) and the
    deposit still reconciles — only a comparison against the contract reveals the
    money withheld. ``recoverable`` = the payer-share shortfall.
    """
    base = _normal_line(rng, payer, cdt, billed)
    contracted = base.allowed                       # what the contract entitles
    target = money(contracted * Decimal(str(rng.uniform(0.08, 0.20))))
    shortfall = money(min(target, base.paid))       # never push paid below zero
    if shortfall <= ZERO:                           # nothing was paid to withhold
        return base, {"underpaid": False, "expected_action": "settled"}

    stated_allowed = money(contracted - shortfall)
    new_paid = money(base.paid - shortfall)

    adjustments: list[Adjustment] = []
    bumped = False
    for a in base.adjustments:
        if a.group_code == "CO" and a.reason_code == CO_FEE_SCHEDULE_CARC:
            adjustments.append(Adjustment(group_code="CO", reason_code=CO_FEE_SCHEDULE_CARC,
                                          amount=money(a.amount + shortfall)))
            bumped = True
        else:
            adjustments.append(a)
    if not bumped:
        adjustments.insert(0, Adjustment(group_code="CO", reason_code=CO_FEE_SCHEDULE_CARC, amount=shortfall))

    line = ClaimLine(
        cdt_code=cdt,
        billed=billed,
        allowed=stated_allowed,
        paid=new_paid,
        adjustments=adjustments,
        patient_responsibility=base.patient_responsibility,
    )
    note = {
        "underpaid": True,
        "contracted_allowed": str(contracted),
        "stated_allowed": str(stated_allowed),
        "recoverable": str(shortfall),
        "expected_action": "review",
    }
    return line, note


def _negate_line(line: ClaimLine) -> ClaimLine:
    return ClaimLine(
        cdt_code=line.cdt_code,
        billed=money(-line.billed),
        allowed=money(-line.allowed),
        paid=money(-line.paid),
        adjustments=[
            Adjustment(group_code=a.group_code, reason_code=a.reason_code, amount=money(-a.amount))
            for a in line.adjustments
        ],
        patient_responsibility=money(-line.patient_responsibility),
    )


def adjudicate_claim(rng: random.Random, raw: RawClaim, payer: str) -> AdjudicatedClaim:
    lines: list[ClaimLine] = []
    notes: dict[int, dict] = {}

    for idx, rl in enumerate(raw.lines):
        if raw.role == ROLE_DENIAL:
            line, note = _denial_line(rng, rl.cdt_code, rl.billed)
            notes[idx] = note
        elif raw.role == ROLE_OVERPAYMENT:
            line, note = _overpayment_line(rng, payer, rl.cdt_code, rl.billed)
            notes[idx] = note
        elif raw.role == ROLE_UNDERPAYMENT:
            line, note = _underpayment_line(rng, payer, rl.cdt_code, rl.billed)
            notes[idx] = note
        else:
            line = _normal_line(rng, payer, rl.cdt_code, rl.billed)
            if raw.role == ROLE_COB:
                notes[idx] = {"cob": True, "expected_action": "bill_secondary"}

        if raw.role == ROLE_REVERSAL:
            line = _negate_line(line)
        lines.append(line)

    if raw.role == ROLE_REVERSAL:
        notes = {i: {"reversal": True, "expected_action": "review"} for i in range(len(lines))}

    billed_total = money(sum((l.billed for l in lines), ZERO))
    paid_total = money(sum((l.paid for l in lines), ZERO))

    claim = Claim(
        claim_id=raw.claim_id,
        patient_ref=raw.patient_ref,
        date_of_service=raw.date_of_service,
        clp_status_code=_STATUS.get(raw.role, "1"),
        billed_total=billed_total,
        paid_total=paid_total,
        lines=lines,
    )
    return AdjudicatedClaim(claim=claim, role=raw.role, payer=payer, notes=notes)
