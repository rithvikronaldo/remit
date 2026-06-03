"""Vision extraction path — for scanned/printed EOBs without a usable text layer.

Sends the PDF to Claude (vision) and forces the ``ExtractedRemittance`` schema as
hard structured output with a per-line confidence. Lazy-imported so the module
loads without langchain. Tests use a stub extractor; the real path runs when an
ANTHROPIC_API_KEY is configured.

Discipline: the model's claim is never believed — it is checked against the
arithmetic invariant downstream (app/extract/extract.py), exactly like the 835.
"""

from __future__ import annotations

import base64
import os
from typing import Protocol

from app.extract.schema import ExtractedRemittance

MODEL = os.getenv("EXTRACTION_MODEL", "claude-haiku-4-5")

VISION_PROMPT = """You are extracting a dental insurance EOB (Explanation of Benefits) into structured data.
Read every claim and every service line. For each line capture cdt_code, billed, allowed, paid,
patient_responsibility, and the list of adjustments (group_code, reason_code, amount).
Set a per-line confidence in [0,1] reflecting how legible the money fields were.
Do not infer values you cannot read — lower the confidence instead. Return the structured ExtractedRemittance."""


class Extractor(Protocol):
    name: str
    def extract(self, pdf_path: str) -> ExtractedRemittance: ...


def build_vision_extractor(model: str = MODEL):
    """Real Claude vision extractor (lazy imports)."""
    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import HumanMessage

    llm = ChatAnthropic(model=model, temperature=0).with_structured_output(ExtractedRemittance)

    class _VisionExtractor:
        name = model

        def extract(self, pdf_path: str) -> ExtractedRemittance:
            with open(pdf_path, "rb") as f:
                b64 = base64.standard_b64encode(f.read()).decode()
            msg = HumanMessage(content=[
                {"type": "document",
                 "source": {"type": "base64", "media_type": "application/pdf", "data": b64}},
                {"type": "text", "text": VISION_PROMPT},
            ])
            return llm.invoke([msg])

    return _VisionExtractor()
