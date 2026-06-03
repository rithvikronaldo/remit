"""Phase 5 — matching engine (link remittance lines to open claim lines)."""

from app.match.engine import MatchResult, OpenClaimLine, OpenClaimRepository, match_remittance

__all__ = ["match_remittance", "MatchResult", "OpenClaimLine", "OpenClaimRepository"]
