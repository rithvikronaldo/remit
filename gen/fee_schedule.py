"""Per-payer, per-CDT allowed amounts — sourced from the practice's contracted fee
schedule (``contracts/fee_schedules.json`` via ``app.contracts``).

The allowed amount drives the contractual write-off (``CO-45 = billed - allowed``).
The generator and the engine read the *same* contract artifact, so the "expected"
allowed the underpayment detector checks against is exactly what a faithfully-paid
line would receive. ``allowed <= billed`` always holds (write-off never negative).
"""

from __future__ import annotations

from decimal import Decimal

from app.contracts import contracted_allowed


def allowed_amount(payer: str, cdt_code: str, billed: Decimal) -> Decimal:
    """The payer's allowed amount for a billed line, per contract. Deterministic."""
    return contracted_allowed(payer, cdt_code, billed)
