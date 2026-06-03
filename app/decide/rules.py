"""Tier 1 — deterministic rules. The unambiguous pairs resolve with zero model
risk and handle the bulk of real-world volume.

Two layers:
  - Claim-status overrides — context the per-adjustment tuple doesn't carry but
    that deterministically decides the action:
      * status 22 (reversal)  → review (reverse a prior settlement, never re-settle)
      * status 2  (secondary) + PR group → bill_secondary (COB), not bill_patient
  - The (group_code, reason_code) RULES map for the routine codes.

Everything else (denials, OA/PI, payer-specific, unknowns) falls through to
retrieval + the grounded LLM.
"""

from __future__ import annotations

from typing import Optional

# (group_code, reason_code) -> (action, billable_to_patient)
RULES: dict[tuple[str, str], tuple[str, bool]] = {
    ("CO", "45"): ("contractual_writeoff", False),   # exceeds fee schedule
    ("CO", "97"): ("contractual_writeoff", False),   # bundled / included
    ("PR", "1"):  ("bill_patient", True),            # deductible
    ("PR", "2"):  ("bill_patient", True),            # coinsurance
    ("PR", "3"):  ("bill_patient", True),            # copay
    ("PR", "119"): ("bill_patient", True),           # benefit maximum reached
}

# Canonical claim statuses (CLP02).
STATUS_SECONDARY = "2"
STATUS_REVERSAL = "22"


def status_override(group_code: str, claim_status: str) -> Optional[tuple[str, str]]:
    """Return (action, rule_id) when claim status deterministically decides it."""
    if claim_status == STATUS_REVERSAL:
        return ("review", "rule:reversal")
    if claim_status == STATUS_SECONDARY and group_code == "PR":
        return ("bill_secondary", "rule:cob-secondary")
    return None


def rule_lookup(group_code: str, reason_code: str) -> Optional[tuple[str, bool]]:
    return RULES.get((group_code, reason_code))
