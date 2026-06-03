"""Phase 4 — PDF EOB extraction (text-layer free path + vision LLM for scans)."""

from app.extract.extract import ExtractResult, confidence_floor, extract_pdf
from app.extract.schema import ExtractedRemittance
from app.extract.text_layer import extract_text_layer, has_text_layer

__all__ = [
    "extract_pdf", "ExtractResult", "confidence_floor",
    "ExtractedRemittance", "extract_text_layer", "has_text_layer",
]
