"""Phase 5 — matching engine.

Links each parsed/extracted remittance line to the open claim line booked at
submission time (Phase 0 seeds these at the billed amount). Match key:
``(payer, patient_ref, date_of_service, cdt_code, billed)`` with small tolerances
(billed within rounding, DOS exact).

Nuances:
  - Split / partial payments — a claim paid across two remittances accumulates
    against the open line(s); never overwrite.
  - Duplicate (cdt, billed) lines within a claim — each open instance is consumed
    once (a multiset), so both get matched.
  - An unmatched line is never force-matched → ``unmatched_line`` exception
    (covers bundling/unbundling where the payer's code composition differs).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from app.models import Claim, ClaimLine, Remittance, money

BILLED_TOLERANCE = Decimal("0.01")


@dataclass
class OpenClaimLine:
    claim_id: str
    payer: str
    patient_ref: str
    date_of_service: date
    cdt_code: str
    billed: Decimal
    paid_to_date: Decimal = money(0)
    match_count: int = 0

    @property
    def status(self) -> str:
        return "matched" if self.match_count else "open"


def _key(payer: str, patient_ref: str, dos: date, cdt_code: str) -> tuple:
    return (payer, patient_ref, dos, cdt_code)


@dataclass
class MatchResult:
    matched: list[dict] = field(default_factory=list)       # {claim_id, cdt, open_line, paid}
    exceptions: list[dict] = field(default_factory=list)    # {reason: unmatched_line, ...}

    @property
    def matched_count(self) -> int:
        return len(self.matched)


class OpenClaimRepository:
    """In-memory store of open claim lines (the DB holds these in production)."""

    def __init__(self) -> None:
        self._by_key: dict[tuple, list[OpenClaimLine]] = {}

    def add(self, line: OpenClaimLine) -> None:
        self._by_key.setdefault(_key(line.payer, line.patient_ref, line.date_of_service, line.cdt_code), []).append(line)

    @classmethod
    def from_claims(cls, payer: str, claims: list[Claim]) -> "OpenClaimRepository":
        repo = cls()
        for c in claims:
            for ln in c.lines:
                repo.add(OpenClaimLine(
                    claim_id=c.claim_id, payer=payer, patient_ref=c.patient_ref,
                    date_of_service=c.date_of_service, cdt_code=ln.cdt_code, billed=ln.billed,
                ))
        return repo

    @classmethod
    def from_golden(cls, golden: dict) -> "OpenClaimRepository":
        repo = cls()
        payer = golden.get("payer", "")
        from datetime import datetime
        for c in golden["claims"]:
            dos = datetime.fromisoformat(c["date_of_service"]).date()
            for ln in c["lines"]:
                repo.add(OpenClaimLine(
                    claim_id=c["claim_id"], payer=payer, patient_ref=c["patient_ref"],
                    date_of_service=dos, cdt_code=ln["cdt_code"], billed=money(ln["billed"]),
                ))
        return repo

    def find(self, payer: str, patient_ref: str, dos: date, line: ClaimLine) -> Optional[OpenClaimLine]:
        candidates = self._by_key.get(_key(payer, patient_ref, dos, line.cdt_code), [])
        # Prefer an unmatched instance whose billed is within tolerance.
        for c in candidates:
            if c.match_count == 0 and abs(c.billed - line.billed) <= BILLED_TOLERANCE:
                return c
        # Otherwise accumulate onto an existing matched instance (split same line).
        for c in candidates:
            if abs(c.billed - line.billed) <= BILLED_TOLERANCE:
                return c
        return None

    def open_lines(self) -> list[OpenClaimLine]:
        return [ln for lines in self._by_key.values() for ln in lines]

    def extend_from_golden(self, golden: dict) -> int:
        """Add open claim lines from a golden-format dict to this repo; returns count added."""
        from datetime import datetime
        payer = golden.get("payer", "")
        n = 0
        for c in golden["claims"]:
            dos = datetime.fromisoformat(c["date_of_service"]).date()
            for ln in c["lines"]:
                self.add(OpenClaimLine(
                    claim_id=c["claim_id"], payer=payer, patient_ref=c["patient_ref"],
                    date_of_service=dos, cdt_code=ln["cdt_code"], billed=money(ln["billed"]),
                ))
                n += 1
        return n


def match_remittance(remit: Remittance, repo: OpenClaimRepository) -> MatchResult:
    result = MatchResult()
    for claim in remit.claims:
        for line in claim.lines:
            open_line = repo.find(remit.payer, claim.patient_ref, claim.date_of_service, line)
            if open_line is None:
                result.exceptions.append({
                    "reason": "unmatched_line",
                    "detail": f"{claim.claim_id} {line.cdt_code} billed {line.billed} "
                              f"(payer {remit.payer}, patient {claim.patient_ref}, dos {claim.date_of_service})",
                })
                continue
            open_line.paid_to_date = money(open_line.paid_to_date + line.paid)
            open_line.match_count += 1
            result.matched.append({
                "claim_id": claim.claim_id, "cdt_code": line.cdt_code,
                "open_line": open_line, "paid": line.paid,
            })
    return result
