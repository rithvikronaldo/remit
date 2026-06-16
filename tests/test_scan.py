"""Phase 4 — scanned-EOB synthesis + routing.

``gen/eob_scan.py`` turns a born-digital EOB into a degraded, image-only PDF so
the vision path has a realistic input. These tests pin the one property the
routing decision depends on: a scanned EOB has *no* text layer, so
``has_text_layer()`` is False and ``extract_pdf`` falls through to vision —
exactly where a clean digital EOB stays on the free text-layer path.

The vision model itself is not exercised here (no API key in CI); its gate is
already covered by the stub tests in ``test_extract.py``. What we prove is that
the right kind of document reaches it.
"""

from __future__ import annotations

import os
import tempfile

from app.extract import has_text_layer
from gen.eob_scan import build_scan_sample, render_scanned_eob


def test_scanned_eob_has_no_text_layer_and_routes_to_vision():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scanned_eob.pdf")
        remit, smudge_text = build_scan_sample(path, seed=7, claims=3)

        # The decision input to extract_pdf's router: no text layer -> vision branch.
        assert has_text_layer(path) is False
        assert os.path.getsize(path) > 10_000  # a real rasterized page, not an empty doc
        assert smudge_text is not None         # a money cell was obscured for fail-closed


def test_clean_twin_keeps_its_text_layer():
    """Contrast: the same remittance rendered digitally *does* carry a text layer."""
    from gen.eob_pdf import render_eob_pdf

    with tempfile.TemporaryDirectory() as d:
        remit, _ = build_scan_sample(os.path.join(d, "scan.pdf"), seed=7, claims=3)
        clean = os.path.join(d, "clean.pdf")
        render_eob_pdf(remit, clean)
        assert has_text_layer(clean) is True


def test_no_smudge_option_skips_obscuring():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scan.pdf")
        _, smudge_text = build_scan_sample(path, seed=7, claims=2, smudge=False)
        assert smudge_text is None
        assert has_text_layer(path) is False


def test_render_is_deterministic_for_a_seed():
    """Same seed → identical page pixels (the PDF wrapper carries a timestamp, so
    compare the rendered image, not the container bytes)."""
    import hashlib
    import random

    import fitz

    from gen.adjudicator import adjudicate_claim
    from gen.builder import assemble
    from gen.catalog import PAYERS
    from gen.claim_factory import RatesConfig, build_claims
    from gen.eob_scan import PAID_DATE

    rng = random.Random(7)
    payer = rng.choice(PAYERS)
    raw = build_claims(rng, 3, RatesConfig(denial=0.34), PAID_DATE)
    remit = assemble([adjudicate_claim(rng, rc, payer) for rc in raw], payer, PAID_DATE)[0]

    def pixels(path):
        h = hashlib.sha256()
        for page in fitz.open(path):
            h.update(page.get_pixmap().samples)
        return h.hexdigest()

    with tempfile.TemporaryDirectory() as d:
        a, b = os.path.join(d, "a.pdf"), os.path.join(d, "b.pdf")
        render_scanned_eob(remit, a, smudge_text="24.85", seed=7)
        render_scanned_eob(remit, b, smudge_text="24.85", seed=7)
        assert pixels(a) == pixels(b)
