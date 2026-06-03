"""Per-payer, per-CDT allowed amounts.

The allowed amount drives the contractual write-off (``CO-45 = billed - allowed``).
We derive it from a per-payer factor against the billed charge and clamp to the
billed amount, so ``allowed <= billed`` always holds (the write-off is never
negative on a normal line). Quantized to cents.
"""

from __future__ import annotations

from decimal import Decimal

from app.models import money
from gen.catalog import PAYER_FACTOR


def allowed_amount(payer: str, cdt_code: str, billed: Decimal) -> Decimal:
    """The payer's allowed amount for a billed line. Deterministic given inputs."""
    factor = PAYER_FACTOR.get(payer, 0.70)
    allowed = money(Decimal(str(factor)) * billed)
    return min(allowed, money(billed))
