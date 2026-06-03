"""Phase 4 orchestrator — produce a canonical ``Remittance`` from an EOB PDF.

  - Digital PDF with a text layer → free, deterministic text-layer extractor.
  - Scanned PDF (no text layer) → vision LLM extractor (needs an API key).

Then the *identical* arithmetic gate used on the 835 path runs: per line,
``billed == paid + Σ adjustments`` and claim totals must sum. The model's claim
is checked against the invariant, never believed. Money fields below a confidence
floor are flagged. Any failure routes to an exception instead of settling.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from app.extract.schema import ExtractedRemittance
from app.extract.text_layer import extract_text_layer, has_text_layer
from app.extract.vision import Extractor
from app.models import Remittance


def confidence_floor() -> float:
    try:
        return float(os.getenv("EXTRACTION_CONFIDENCE_FLOOR", "0.90"))
    except ValueError:
        return 0.90


@dataclass
class ExtractResult:
    remittance: Remittance
    source: str                       # "text_layer" | "vision"
    min_confidence: float = 1.0
    exceptions: list[dict] = field(default_factory=list)


def _gate(remit: Remittance, min_confidence: float, floor: float) -> list[dict]:
    exceptions = [
        {"reason": "extraction_arithmetic_break", "detail": v}
        for v in remit.invariant_violations()
    ]
    if min_confidence < floor:
        exceptions.append({
            "reason": "low_confidence_extraction",
            "detail": f"min field confidence {min_confidence:.2f} < floor {floor:.2f}",
        })
    return exceptions


def extract_pdf(
    pdf_path: str,
    *,
    extractor: Optional[Extractor] = None,
    force_vision: bool = False,
    floor: Optional[float] = None,
) -> ExtractResult:
    floor = confidence_floor() if floor is None else floor

    if not force_vision and extractor is None and has_text_layer(pdf_path):
        remit = extract_text_layer(pdf_path)
        return ExtractResult(remittance=remit, source="text_layer", min_confidence=1.0,
                             exceptions=_gate(remit, 1.0, floor))

    # Vision path (scanned PDFs / explicit override). Requires an extractor or key.
    if extractor is None:
        from app.extract.vision import build_vision_extractor
        extractor = build_vision_extractor()
    extracted: ExtractedRemittance = extractor.extract(pdf_path)
    remit = extracted.to_canonical()
    min_conf = extracted.min_confidence()
    return ExtractResult(remittance=remit, source="vision", min_confidence=min_conf,
                         exceptions=_gate(remit, min_conf, floor))
