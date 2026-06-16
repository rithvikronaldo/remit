"""Emit the ground-truth JSON (the golden-set seed): expected per-adjustment
actions and per-line settlements, labelled from the adjudicated claims. This is
the oracle the Phase 2 decision eval and the Phase 9 pipeline eval score against.

The golden is the *full* canonical truth keyed by claim/line, independent of how
lines were split across 835 files — so a parser that reassembles by ``claim_id``
can be checked against it directly.
"""

from __future__ import annotations

from decimal import Decimal

from app.models import ClaimLine, money
from gen.adjudicator import AdjudicatedClaim
from gen.claim_factory import ROLE_COB, ROLE_REVERSAL


def _adjustment_action(adj, *, is_denial: bool, cob: bool, denial_action: str, reversal: bool):
    if reversal:
        return "review", False
    if adj.group_code == "CO":
        if is_denial:
            return denial_action, False
        return "contractual_writeoff", False
    if adj.group_code == "PR":
        return ("bill_secondary", False) if cob else ("bill_patient", True)
    # OA / PI — payer-initiated / other; flag for review (e.g. overpayment OA-23).
    return "review", False


def _line_golden(line: ClaimLine, role: str, note: dict) -> dict:
    W = line.sum_group("CO")
    R = line.sum_group("PR")
    cob = bool(note.get("cob"))
    denial_carc = note.get("denial_carc")
    is_denial = denial_carc is not None
    overpayment = bool(note.get("overpayment"))
    underpaid = bool(note.get("underpaid"))
    reversal = role == ROLE_REVERSAL

    adjustments = []
    for adj in line.adjustments:
        action, billable = _adjustment_action(
            adj, is_denial=is_denial, cob=cob,
            denial_action=note.get("expected_action", "review"), reversal=reversal,
        )
        adjustments.append({
            "group": adj.group_code,
            "reason": adj.reason_code,
            "amount": f"{adj.amount:.2f}",
            "expected_action": action,
            "billable_to_patient": billable,
        })

    # Expected settlement buckets + status, driven by the decided action.
    expected_exception = None
    if is_denial:
        if note.get("expected_action") == "appeal":
            settlement = {"insurance_paid": "0.00", "contractual_writeoff": "0.00",
                          "patient_responsibility": "0.00", "secondary_responsibility": "0.00"}
            status = "appealed"
        else:  # contractual_writeoff (e.g. timely-filing)
            settlement = {"insurance_paid": "0.00", "contractual_writeoff": f"{W:.2f}",
                          "patient_responsibility": "0.00", "secondary_responsibility": "0.00"}
            status = "settled"
    elif overpayment:
        settlement = {"insurance_paid": f"{line.paid:.2f}", "contractual_writeoff": f"{W:.2f}",
                      "patient_responsibility": f"{R:.2f}", "secondary_responsibility": "0.00"}
        status = "queued"
        expected_exception = "overpayment"
    elif cob:
        settlement = {"insurance_paid": f"{line.paid:.2f}", "contractual_writeoff": f"{W:.2f}",
                      "patient_responsibility": "0.00", "secondary_responsibility": f"{R:.2f}"}
        status = "settled"
    elif underpaid:
        # Money moved and the line balances; it's flagged because the payer paid
        # below the contracted rate (only the contract reveals it).
        settlement = {"insurance_paid": f"{line.paid:.2f}", "contractual_writeoff": f"{W:.2f}",
                      "patient_responsibility": f"{R:.2f}", "secondary_responsibility": "0.00"}
        status = "queued"
        expected_exception = "underpayment"
    else:  # normal / split / reversal
        settlement = {"insurance_paid": f"{line.paid:.2f}", "contractual_writeoff": f"{W:.2f}",
                      "patient_responsibility": f"{R:.2f}", "secondary_responsibility": "0.00"}
        status = "settled"

    out = {
        "cdt_code": line.cdt_code,
        "billed": f"{line.billed:.2f}",
        "allowed": f"{line.allowed:.2f}",
        "paid": f"{line.paid:.2f}",
        "patient_responsibility": f"{line.patient_responsibility:.2f}",
        "adjustments": adjustments,
        "expected_settlement": settlement,
        "expected_status": status,
    }
    if expected_exception:
        out["expected_exception"] = expected_exception
    if underpaid:
        out["underpayment"] = {
            "contracted_allowed": f"{money(note['contracted_allowed']):.2f}",
            "stated_allowed": f"{money(note['stated_allowed']):.2f}",
            "recoverable": f"{money(note['recoverable']):.2f}",
        }
    return out


def build_golden(adjudicated: list[AdjudicatedClaim], remittances, seed: int) -> dict:
    payer = remittances[0].payer
    claims = []
    for ac in adjudicated:
        claim = ac.claim
        claims.append({
            "claim_id": claim.claim_id,
            "patient_ref": claim.patient_ref,
            "date_of_service": claim.date_of_service.isoformat(),
            "clp_status_code": claim.clp_status_code,
            "role": ac.role,
            "billed_total": f"{claim.billed_total:.2f}",
            "paid_total": f"{claim.paid_total:.2f}",
            "lines": [_line_golden(l, ac.role, ac.notes.get(i, {})) for i, l in enumerate(claim.lines)],
        })

    return {
        "seed": seed,
        "payer": payer,
        "paid_date": remittances[0].paid_date.isoformat(),
        "remittances": [
            {"trn": r.trn, "eft_amount": f"{r.eft_amount:.2f}",
             "plb_amount": f"{r.plb_amount:.2f}", "claim_ids": [c.claim_id for c in r.claims]}
            for r in remittances
        ],
        "claims": claims,
    }
