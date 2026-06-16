"""Build raw claims (patients, dates of service, lines with billed amounts) and
assign each an edge-case role from the configured rates. The adjudicator then
computes allowed/paid/adjustments and applies the role.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from app.models import money
from gen.catalog import CDT_CATALOG

# Edge-case roles (each maps to a §14 PRD case).
ROLE_NORMAL = "normal"
ROLE_DENIAL = "denial"
ROLE_REVERSAL = "reversal"
ROLE_COB = "cob"
ROLE_OVERPAYMENT = "overpayment"
ROLE_SPLIT = "split"
ROLE_UNDERPAYMENT = "underpayment"   # payer allows below the contracted rate (silent shortfall)


@dataclass
class RawLine:
    cdt_code: str
    billed: Decimal


@dataclass
class RawClaim:
    claim_id: str
    patient_ref: str
    date_of_service: date
    role: str = ROLE_NORMAL
    lines: list[RawLine] = field(default_factory=list)


@dataclass
class RatesConfig:
    denial: float = 0.0
    reversal: float = 0.0
    cob: float = 0.0
    overpayment: float = 0.0
    split: float = 0.0
    underpayment: float = 0.0


def _assign_role(rng: random.Random, rates: RatesConfig) -> str:
    """Pick at most one edge-case role for a claim (mutually exclusive)."""
    roll = rng.random()
    cumulative = 0.0
    for role, rate in (
        (ROLE_DENIAL, rates.denial),
        (ROLE_REVERSAL, rates.reversal),
        (ROLE_COB, rates.cob),
        (ROLE_OVERPAYMENT, rates.overpayment),
        (ROLE_SPLIT, rates.split),
        (ROLE_UNDERPAYMENT, rates.underpayment),
    ):
        cumulative += rate
        if roll < cumulative:
            return role
    return ROLE_NORMAL


def build_claims(
    rng: random.Random,
    count: int,
    rates: RatesConfig,
    base_date: date,
) -> list[RawClaim]:
    claims: list[RawClaim] = []
    for i in range(1, count + 1):
        role = _assign_role(rng, rates)
        # Split needs ≥2 lines so the claim can be partitioned across remittances.
        n_lines = rng.randint(2, 3) if role == ROLE_SPLIT else rng.randint(1, 3)
        dos = base_date - timedelta(days=rng.randint(7, 45))
        lines: list[RawLine] = []
        for _ in range(n_lines):
            cdt, _desc, (lo, hi) = rng.choice(CDT_CATALOG)
            billed = money(rng.randint(lo, hi))
            lines.append(RawLine(cdt_code=cdt, billed=billed))
        claims.append(
            RawClaim(
                claim_id=f"CLAIM{i:03d}",
                patient_ref=f"MEMBER{i:03d}",
                date_of_service=dos,
                role=role,
                lines=lines,
            )
        )
    return claims
