"""Phase 3 — deterministic X12 835 parser → canonical ``Remittance``.

No LLM: the 835 is already structured. Split on segment/element/component
separators, validate the envelope and control numbers, then walk the hierarchy:

    BPR → payment_method, eft_amount, paid_date
    TRN → trn (reassociation key)
    N1*PR / N1*PE → payer / payee
    CLP → a Claim (id, status, billed total, paid total, patient responsibility)
    NM1*QC → patient_ref
    SVC → a ClaimLine (cdt, billed, paid)
    CAS (claim- or line-level) → repeating (group, reason, amount) adjustments
    DTM*472 → date of service
    PLB → provider-level adjustment (held for reconciliation)

The single best test is the round-trip: generator → 835 → parser → canonical must
equal the generator's original Remittance (proves writer and parser at once).

Per-line arithmetic is gated (``billed == paid + Σ adjustments``); violations are
returned as ``extraction_arithmetic_break`` exceptions rather than silently parsed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from app.models import Adjustment, Claim, ClaimLine, Remittance, money

SEG, ELEM, COMP = "~", "*", ":"


class X12ParseError(ValueError):
    """Raised for malformed envelopes / control-number mismatches."""


@dataclass
class ParseResult:
    remittance: Remittance
    content_hash: str
    exceptions: list[dict] = field(default_factory=list)


def _segments(text: str) -> list[list[str]]:
    out = []
    for raw in text.replace("\n", "").split(SEG):
        raw = raw.strip()
        if raw:
            out.append(raw.split(ELEM))
    return out


def _d(value: str) -> Decimal:
    return money(value or "0")


def _ccyymmdd(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def _validate_envelope(segs: list[list[str]]) -> None:
    ids = [s[0] for s in segs]
    if not ids or ids[0] != "ISA" or ids[-1] != "IEA":
        raise X12ParseError("missing ISA/IEA envelope")
    for required in ("GS", "ST", "SE", "GE"):
        if required not in ids:
            raise X12ParseError(f"missing {required} segment")
    st = next(s for s in segs if s[0] == "ST")
    se = next(s for s in segs if s[0] == "SE")
    if st[1] != "835":
        raise X12ParseError(f"not an 835 transaction (ST01={st[1]})")
    if len(st) > 2 and len(se) > 2 and st[2] != se[2]:
        raise X12ParseError(f"ST/SE control mismatch ({st[2]} != {se[2]})")


def _cas_adjustments(seg: list[str]) -> list[Adjustment]:
    """CAS*<group>*<reason>*<amount>[*<reason>*<amount>...] — quantity is not
    emitted by our writer, so sets are (reason, amount) pairs after the group."""
    group = seg[1]
    adjustments = []
    rest = seg[2:]
    for i in range(0, len(rest) - 1, 2):
        reason, amount = rest[i], rest[i + 1]
        if reason == "":
            continue
        adjustments.append(Adjustment(group_code=group, reason_code=reason, amount=_d(amount)))
    return adjustments


def parse_835(text: str) -> ParseResult:
    import hashlib

    content_hash = hashlib.sha256(text.encode()).hexdigest()
    segs = _segments(text)
    _validate_envelope(segs)

    payer = payee = trn = ""
    payment_method = "ACH"
    eft_amount = money(0)
    paid_date: Optional[date] = None
    plb_amount = money(0)

    claims: list[Claim] = []
    cur_claim: Optional[dict] = None     # accumulates fields + lines
    cur_line: Optional[ClaimLine] = None

    def flush_line():
        nonlocal cur_line
        if cur_line is not None and cur_claim is not None:
            cur_claim["lines"].append(cur_line)
            cur_line = None

    def flush_claim():
        nonlocal cur_claim
        flush_line()
        if cur_claim is not None:
            claims.append(Claim(
                claim_id=cur_claim["claim_id"],
                patient_ref=cur_claim["patient_ref"],
                date_of_service=cur_claim["dos"],
                clp_status_code=cur_claim["status"],
                billed_total=cur_claim["billed_total"],
                paid_total=cur_claim["paid_total"],
                lines=cur_claim["lines"],
            ))
            cur_claim = None

    for seg in segs:
        tag = seg[0]
        if tag == "BPR":
            payment_method = seg[4]
            eft_amount = _d(seg[2])
            if len(seg) > 16 and seg[16]:
                paid_date = _ccyymmdd(seg[16])
        elif tag == "TRN":
            trn = seg[2]
        elif tag == "N1":
            if seg[1] == "PR":
                payer = seg[2]
            elif seg[1] == "PE":
                payee = seg[2]
        elif tag == "CLP":
            flush_claim()
            cur_claim = {
                "claim_id": seg[1], "status": seg[2],
                "billed_total": _d(seg[3]), "paid_total": _d(seg[4]),
                "patient_ref": "", "dos": None, "lines": [],
            }
        elif tag == "NM1" and seg[1] == "QC" and cur_claim is not None:
            cur_claim["patient_ref"] = seg[-1]
        elif tag == "SVC" and cur_claim is not None:
            flush_line()
            cdt = seg[1].split(COMP)[-1]
            cur_line = ClaimLine(
                cdt_code=cdt, billed=_d(seg[2]), paid=_d(seg[3]),
                allowed=money(0), adjustments=[], patient_responsibility=money(0),
            )
        elif tag == "CAS":
            adjustments = _cas_adjustments(seg)
            if cur_line is not None:
                cur_line.adjustments.extend(adjustments)
            elif cur_claim is not None:
                cur_claim.setdefault("claim_cas", []).extend(adjustments)
        elif tag == "DTM" and seg[1] == "472" and cur_claim is not None:
            if cur_claim["dos"] is None:
                cur_claim["dos"] = _ccyymmdd(seg[2])
        elif tag == "PLB":
            # writer emits the negation of the EFT-additive plb_amount
            plb_amount = money(-_d(seg[-1]))

    flush_claim()

    # Derive allowed + patient_responsibility per line from its adjustments
    # (the 835 carries them in CAS, not as standalone elements).
    for claim in claims:
        for line in claim.lines:
            line.allowed = money(line.billed - line.sum_group("CO", "PI"))
            line.patient_responsibility = line.sum_group("PR")

    remit = Remittance(
        payer=payer, trn=trn, payment_method=payment_method,
        eft_amount=eft_amount, paid_date=paid_date or date(1970, 1, 1),
        payee=payee or "BRIGHT SMILE DENTAL", claims=claims, plb_amount=plb_amount,
    )

    exceptions = [
        {"reason": "extraction_arithmetic_break", "detail": v}
        for v in remit.invariant_violations()
    ]
    return ParseResult(remittance=remit, content_hash=content_hash, exceptions=exceptions)
