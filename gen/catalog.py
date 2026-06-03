"""CDT procedure catalog, payer pool, and CARC/group-code pools used by the
generator. Kept small but realistic — enough to exercise every §14 edge case.
"""

from __future__ import annotations

# (CDT code, description, (billed_min, billed_max)) — realistic dental ranges.
CDT_CATALOG: list[tuple[str, str, tuple[int, int]]] = [
    ("D0120", "Periodic oral evaluation", (45, 75)),
    ("D0150", "Comprehensive oral evaluation", (80, 130)),
    ("D0210", "Intraoral radiographs, full series", (120, 200)),
    ("D0274", "Bitewings, four films", (55, 90)),
    ("D1110", "Prophylaxis, adult", (90, 150)),
    ("D1206", "Topical fluoride varnish", (35, 60)),
    ("D2391", "Resin composite, one surface, posterior", (160, 260)),
    ("D2740", "Crown, porcelain/ceramic", (900, 1500)),
    ("D2950", "Core buildup, including pins", (220, 360)),
    ("D4341", "Periodontal scaling/root planing, per quadrant", (200, 320)),
]

PAYERS: list[str] = [
    "DELTA DENTAL OF EXAMPLE",
    "CIGNA DENTAL",
    "AETNA DENTAL",
    "METLIFE DENTAL",
]

# Per-payer fee-schedule factor (allowed ≈ billed * factor); see fee_schedule.py.
PAYER_FACTOR: dict[str, float] = {
    "DELTA DENTAL OF EXAMPLE": 0.70,
    "CIGNA DENTAL": 0.65,
    "AETNA DENTAL": 0.75,
    "METLIFE DENTAL": 0.68,
}

# X12 sender/receiver ids per payer (ISA/GS envelope), kept stable per payer.
PAYER_X12_ID: dict[str, str] = {
    "DELTA DENTAL OF EXAMPLE": "PAYER0001",
    "CIGNA DENTAL": "PAYER0002",
    "AETNA DENTAL": "PAYER0003",
    "METLIFE DENTAL": "PAYER0004",
}

# Denial CARCs (CLP status 4). action = the golden-set expected action for the
# denial; the Phase 2 decision layer must reach the same answer via RAG/playbook.
DENIAL_CARCS: dict[str, dict] = {
    "197": {"group": "CO", "gloss": "Precertification/authorization absent", "action": "appeal"},
    "29":  {"group": "CO", "gloss": "Time limit for filing has expired", "action": "contractual_writeoff"},
    "50":  {"group": "CO", "gloss": "Non-covered: not deemed medically necessary", "action": "appeal"},
    "16":  {"group": "CO", "gloss": "Claim lacks information / submission error", "action": "appeal"},
}

# Patient-responsibility CARCs (the routine PR bulk).
PR_CARCS = {
    "1": "Deductible amount",
    "2": "Coinsurance amount",
    "3": "Co-payment amount",
}

# Contractual CARC for the standard fee-schedule write-off.
CO_FEE_SCHEDULE_CARC = "45"   # Charge exceeds fee schedule / max allowable
OVERPAYMENT_CARC = "23"       # OA-23: impact of prior payer adjudication / adjustment
