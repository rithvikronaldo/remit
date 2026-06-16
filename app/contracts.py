"""The practice's contracted payer fee schedules — a first-class, auditable artifact.

The contracted "allowed" amount used to live as a hidden generator constant
(``PAYER_FACTOR`` in ``gen/catalog.py``). It is promoted here — into ``app/`` and a
data file (``contracts/fee_schedules.json``) — because it is *knowledge the engine
reasons over*, not test scaffolding. The underpayment detector (Phase: revenue
integrity) compares each EOB's *stated* ``allowed`` against this contract to find
money a payer withheld below the rate it agreed to — a **contract** break that the
arithmetic invariant and reconciliation can't see (the line still balances and the
deposit still ties; only the contract reveals the shortfall). Corpus-as-brain: the
practice's contracts are data, swappable without code changes.

The contracted allowed for a line is ``factor * billed`` (a percent of the billed
charge), clamped to ``billed``, unless a fixed per-CDT amount overrides the factor.
This is the *same* math that produced normal-line allowed amounts before, so existing
fixtures are byte-identical — only now it is sourced from an auditable file shared by
the generator and the engine alike.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from app.models import money

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "contracts" / "fee_schedules.json"


def _contracts_path() -> str:
    return os.getenv("REMIT_CONTRACTS_PATH", str(_DEFAULT_PATH))


@lru_cache(maxsize=8)
def _load(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _contracts() -> dict:
    return _load(_contracts_path())


def contracted_allowed(payer: str, cdt_code: str, billed: Decimal) -> Decimal:
    """The contract's expected allowed amount for a billed line. Deterministic.

    A fixed per-CDT contracted amount wins if present; otherwise the per-payer
    factor of the billed charge. Always clamped to ``billed`` (allowed <= billed).
    """
    data = _contracts()
    entry = data.get("payers", {}).get(payer, {})
    cdt_amounts = entry.get("cdt_amounts", {})
    if cdt_code in cdt_amounts:
        return min(money(cdt_amounts[cdt_code]), money(billed))
    factor = entry.get("factor", data.get("default_factor", 0.70))
    return min(money(Decimal(str(factor)) * billed), money(billed))


def contracted_payers() -> list[str]:
    """Payers for which the practice has a contracted fee schedule on file."""
    return list(_contracts().get("payers", {}).keys())
