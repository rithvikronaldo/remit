"""Serialize a canonical ``Remittance`` to a valid X12 835.

Hand-rolled and fully controllable (the PRD recommends this over pyx12 for scope).
The Phase 3 parser round-trips against this writer: generator → 835 → parser →
canonical must equal the original. Envelope dates derive from ``paid_date`` (not
wall clock) so output is seed-reproducible.
"""

from __future__ import annotations

from decimal import Decimal

from app.models import Claim, ClaimLine, Remittance, money
from gen.catalog import PAYER_X12_ID

ELEM = "*"
SEG = "~"
COMP = ":"
RECEIVER_ID = "PRACTICE0001"
PAYEE_NPI = "1234567890"


def _amt(value: Decimal) -> str:
    return f"{money(value):.2f}"


def _isa(remit: Remittance) -> str:
    sender = PAYER_X12_ID.get(remit.payer, "PAYER0000").ljust(15)
    receiver = RECEIVER_ID.ljust(15)
    yymmdd = remit.paid_date.strftime("%y%m%d")
    return ELEM.join([
        "ISA", "00", " " * 10, "00", " " * 10,
        "ZZ", sender, "ZZ", receiver,
        yymmdd, "1200", "^", "00501", "000000001", "0", "P", COMP,
    ]) + SEG


def _claim_patient_resp(claim: Claim) -> Decimal:
    return money(sum((l.sum_group("PR") for l in claim.lines), Decimal("0")))


def _cas_segments(line: ClaimLine) -> list[str]:
    """One CAS per group code, packing that group's (reason, amount) triples."""
    by_group: dict[str, list] = {}
    for adj in line.adjustments:
        by_group.setdefault(adj.group_code, []).append(adj)
    segments = []
    for group in ("CO", "PR", "OA", "PI"):
        adjs = by_group.get(group)
        if not adjs:
            continue
        parts = ["CAS", group]
        for adj in adjs:
            parts += [adj.reason_code, _amt(adj.amount)]
        segments.append(ELEM.join(parts) + SEG)
    return segments


def _claim_segments(claim: Claim, lx: int) -> list[str]:
    digits = "".join(c for c in claim.claim_id if c.isdigit()) or "0"
    icn = f"ICN{digits.zfill(6)}"
    segs = [
        ELEM.join(["LX", str(lx)]) + SEG,
        ELEM.join([
            "CLP", claim.claim_id, claim.clp_status_code,
            _amt(claim.billed_total), _amt(claim.paid_total),
            _amt(_claim_patient_resp(claim)), "12", icn, "11",
        ]) + SEG,
        ELEM.join(["NM1", "QC", "1", "PATIENT", claim.claim_id[-3:], "", "", "", "MI", claim.patient_ref]) + SEG,
    ]
    dos = claim.date_of_service.strftime("%Y%m%d")
    for line in claim.lines:
        segs.append(
            ELEM.join(["SVC", f"AD{COMP}{line.cdt_code}", _amt(line.billed), _amt(line.paid), "", "1"]) + SEG
        )
        segs.extend(_cas_segments(line))
        segs.append(ELEM.join(["DTM", "472", dos]) + SEG)
    return segs


def write_835(remit: Remittance) -> str:
    ccyymmdd = remit.paid_date.strftime("%Y%m%d")
    sender = PAYER_X12_ID.get(remit.payer, "PAYER0000")

    header = [
        _isa(remit),
        ELEM.join(["GS", "HP", sender, RECEIVER_ID, ccyymmdd, "1200", "1", "X", "005010X221A1"]) + SEG,
    ]
    # Segments counted for SE run from ST through SE inclusive.
    body = [
        ELEM.join(["ST", "835", "0001"]) + SEG,
        ELEM.join([
            "BPR", "I", _amt(remit.eft_amount), "C", remit.payment_method, "CCP",
            "01", "999988880", "DA", "123456789", "1512345678", "",
            "01", "999988880", "DA", "987654321", ccyymmdd,
        ]) + SEG,
        ELEM.join(["TRN", "1", remit.trn, "1512345678"]) + SEG,
        ELEM.join(["N1", "PR", remit.payer]) + SEG,
        ELEM.join(["N1", "PE", remit.payee, "XX", PAYEE_NPI]) + SEG,
    ]
    for i, claim in enumerate(remit.claims, start=1):
        body.extend(_claim_segments(claim, i))

    if remit.plb_amount != money(0):
        # X12 convention: a positive PLB amount *reduces* the payment, so the
        # segment amount is the negation of our (EFT-additive) plb_amount.
        body.append(
            ELEM.join(["PLB", PAYEE_NPI, ccyymmdd, f"FB{COMP}", _amt(-remit.plb_amount)]) + SEG
        )

    se = ELEM.join(["SE", str(len(body) + 1), "0001"]) + SEG  # +1 for the SE segment itself
    body.append(se)

    trailer = [
        ELEM.join(["GE", "1", "1"]) + SEG,
        ELEM.join(["IEA", "1", "000000001"]) + SEG,
    ]
    return "\n".join(header + body + trailer) + "\n"
