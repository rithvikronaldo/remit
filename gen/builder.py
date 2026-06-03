"""Assemble adjudicated claims into one or more ``Remittance`` objects.

Most runs produce a single remittance. Split-payment claims have their lines
partitioned across a second remittance (same ``claim_id`` in both) so the Phase 5
matching engine can exercise accumulation. Provider-level adjustments (PLB) are
attached to the primary remittance and folded into its EFT total.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models import Claim, Remittance, money
from gen.adjudicator import AdjudicatedClaim
from gen.claim_factory import ROLE_SPLIT

ZERO = money(0)


def _claim_from_lines(src: Claim, lines: list) -> Claim:
    return Claim(
        claim_id=src.claim_id,
        patient_ref=src.patient_ref,
        date_of_service=src.date_of_service,
        clp_status_code=src.clp_status_code,
        billed_total=money(sum((l.billed for l in lines), ZERO)),
        paid_total=money(sum((l.paid for l in lines), ZERO)),
        lines=lines,
    )


def _trn(paid_date: date, seq: int) -> str:
    return f"EFT{paid_date.strftime('%Y%m%d')}{seq:03d}"


def assemble(
    adjudicated: list[AdjudicatedClaim],
    payer: str,
    paid_date: date,
    plb_amount: Decimal = ZERO,
) -> list[Remittance]:
    primary_claims: list[Claim] = []
    remainder_claims: list[Claim] = []

    for ac in adjudicated:
        if ac.role == ROLE_SPLIT and len(ac.claim.lines) >= 2:
            half = len(ac.claim.lines) // 2
            primary_claims.append(_claim_from_lines(ac.claim, ac.claim.lines[:half]))
            remainder_claims.append(_claim_from_lines(ac.claim, ac.claim.lines[half:]))
        else:
            primary_claims.append(ac.claim)

    plb_amount = money(plb_amount)
    primary = Remittance(
        payer=payer,
        trn=_trn(paid_date, 1),
        payment_method="ACH",
        eft_amount=money(sum((c.paid_total for c in primary_claims), ZERO) + plb_amount),
        paid_date=paid_date,
        claims=primary_claims,
        plb_amount=plb_amount,
    )
    remittances = [primary]

    if remainder_claims:
        remittances.append(
            Remittance(
                payer=payer,
                trn=_trn(paid_date, 2),
                payment_method="ACH",
                eft_amount=money(sum((c.paid_total for c in remainder_claims), ZERO)),
                paid_date=paid_date,
                claims=remainder_claims,
            )
        )
    return remittances
