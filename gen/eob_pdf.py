"""Render a ``Remittance`` to a human-readable EOB PDF (the extraction path's
input, Phase 4). Same numbers as the 835 — the PDF and 835 are two views of one
truth. Uses reportlab; importing this module does not require reportlab until
``render_eob_pdf`` is called.
"""

from __future__ import annotations

from app.models import Remittance


def render_eob_pdf(remit: Remittance, path: str) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    story = []
    story.append(Paragraph("EXPLANATION OF BENEFITS", styles["Title"]))
    story.append(Paragraph(
        f"Payer: {remit.payer} &nbsp;&nbsp; Payee: {remit.payee}<br/>"
        f"TRN (trace #): {remit.trn} &nbsp;&nbsp; Payment: {remit.payment_method} "
        f"&nbsp;&nbsp; EFT: ${remit.eft_amount:.2f} &nbsp;&nbsp; Date: {remit.paid_date}",
        styles["Normal"],
    ))
    story.append(Spacer(1, 0.2 * inch))

    for claim in remit.claims:
        story.append(Paragraph(
            f"<b>Claim {claim.claim_id}</b> — patient {claim.patient_ref}, "
            f"DOS {claim.date_of_service}, status {claim.clp_status_code}",
            styles["Heading4"],
        ))
        data = [["CDT", "Billed", "Allowed", "Paid", "Adjustments", "Pt Resp"]]
        for line in claim.lines:
            adj = "; ".join(f"{a.group_code}-{a.reason_code} {a.amount:.2f}" for a in line.adjustments)
            data.append([
                line.cdt_code, f"{line.billed:.2f}", f"{line.allowed:.2f}",
                f"{line.paid:.2f}", adj or "—", f"{line.patient_responsibility:.2f}",
            ])
        table = Table(data, colWidths=[0.8 * inch, 0.8 * inch, 0.8 * inch, 0.8 * inch, 2.4 * inch, 0.8 * inch])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#222")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ]))
        story.append(table)
        story.append(Spacer(1, 0.15 * inch))

    if remit.plb_amount != 0:
        story.append(Paragraph(f"Provider-level adjustment (PLB): {remit.plb_amount:.2f}", styles["Italic"]))

    SimpleDocTemplate(path, pagesize=letter).build(story)
