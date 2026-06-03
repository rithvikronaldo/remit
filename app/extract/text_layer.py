"""Free, deterministic text-layer extractor for digital EOB PDFs.

Many ERA-companion EOBs are generated (not scanned) and carry a clean text layer
— the PRD's "if the PDF has a text layer, pull it" path. Our generated fixtures
are exactly this case, so we can extract them with full fidelity and zero API
cost. Scanned EOBs (no usable text layer) go to the vision LLM path instead.

The reportlab table extracts row-major (one cell per line), so each data row is
six consecutive lines: cdt, billed, allowed, paid, adjustments, patient_resp.
Extraction confidence is 1.0 for a clean digital text layer.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from app.models import Adjustment, Claim, ClaimLine, Remittance, money

_CLAIM_RE = re.compile(r"Claim (\S+) .*patient (\w+), DOS ([\d-]+), status (\w+)")
_TRN_RE = re.compile(r"TRN \(trace #\):\s*(\S+)\s+Payment:\s*(\w+)\s+EFT:\s*\$([\-\d.]+)\s+Date:\s*([\d-]+)")
_PAYER_RE = re.compile(r"Payer:\s*(.+?)\s{2,}Payee:\s*(.+)")
_PLB_RE = re.compile(r"Provider-level adjustment \(PLB\):\s*([\-\d.]+)")
_CDT_RE = re.compile(r"^D\d{4}[A-Z]?$")
_NONE_ADJ = {"—", "-", "–", ""}


def _pdf_lines(pdf_path: str) -> list[str]:
    from pypdf import PdfReader
    reader = PdfReader(pdf_path)
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    return [ln.strip() for ln in text.split("\n") if ln.strip()]


def _parse_adjustments(cell: str) -> list[Adjustment]:
    if cell.strip() in _NONE_ADJ:
        return []
    out = []
    for part in cell.split(";"):
        part = part.strip()
        if not part:
            continue
        code, amount = part.rsplit(" ", 1)        # "CO-45 81.00" / "OA-23 -25.00"
        group, reason = code.split("-", 1)
        out.append(Adjustment(group_code=group, reason_code=reason, amount=money(amount)))
    return out


def has_text_layer(pdf_path: str) -> bool:
    try:
        lines = _pdf_lines(pdf_path)
    except Exception:
        return False
    return any(l.startswith("Claim ") for l in lines)


def extract_text_layer(pdf_path: str) -> Remittance:
    lines = _pdf_lines(pdf_path)
    payer = payee = trn = ""
    payment_method = "ACH"
    eft_amount = money(0)
    plb_amount = money(0)
    paid_date = None
    claims: list[Claim] = []
    cur: dict | None = None

    def flush():
        nonlocal cur
        if cur is None:
            return
        lns = cur["lines"]
        claims.append(Claim(
            claim_id=cur["claim_id"], patient_ref=cur["patient_ref"],
            date_of_service=cur["dos"], clp_status_code=cur["status"],
            billed_total=money(sum((l.billed for l in lns), Decimal("0"))),
            paid_total=money(sum((l.paid for l in lns), Decimal("0"))),
            lines=lns,
        ))
        cur = None

    i = 0
    while i < len(lines):
        line = lines[i]

        m = _PAYER_RE.search(line)
        if m and not payer:
            payer, payee = m.group(1).strip(), m.group(2).strip()
            i += 1
            continue
        m = _TRN_RE.search(line)
        if m:
            trn, payment_method = m.group(1), m.group(2)
            eft_amount = money(m.group(3))
            paid_date = datetime.strptime(m.group(4), "%Y-%m-%d").date()
            i += 1
            continue
        m = _PLB_RE.search(line)
        if m:
            plb_amount = money(m.group(1))
            i += 1
            continue
        m = _CLAIM_RE.search(line)
        if m:
            flush()
            cur = {
                "claim_id": m.group(1), "patient_ref": m.group(2),
                "dos": datetime.strptime(m.group(3), "%Y-%m-%d").date(),
                "status": m.group(4), "lines": [],
            }
            i += 1
            continue
        if line == "CDT":          # table header row → skip its 6 labels
            i += 6
            continue
        if cur is not None and _CDT_RE.match(line) and i + 5 < len(lines):
            cdt = line
            billed, allowed, paid, adj_cell, pr = lines[i + 1:i + 6]
            cur["lines"].append(ClaimLine(
                cdt_code=cdt, billed=money(billed), allowed=money(allowed), paid=money(paid),
                adjustments=_parse_adjustments(adj_cell), patient_responsibility=money(pr),
                extraction_confidence=1.0,
            ))
            i += 6
            continue
        i += 1

    flush()
    return Remittance(
        payer=payer, trn=trn, payment_method=payment_method, eft_amount=eft_amount,
        paid_date=paid_date, payee=payee or "BRIGHT SMILE DENTAL",
        claims=claims, plb_amount=plb_amount,
    )
