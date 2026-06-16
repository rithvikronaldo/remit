"""Synthesize a *scanned* EOB — the input that actually exercises the vision path.

Our ``--pdf`` EOBs are born digital: reportlab writes a clean text layer, so
``has_text_layer()`` is True and extraction takes the free, deterministic
text-layer path (``app/extract/text_layer.py``). A real practice also receives
EOBs as **scans and faxes**: a page image with no text layer at all. Those are
the documents the vision LLM path (``app/extract/vision.py``) exists for, and
until now nothing in the repo produced one — which is why the vision branch was
only ever exercised by stubs.

This module manufactures that case from the *same* canonical ``Remittance``:
render the clean EOB, rasterize each page to an image (PyMuPDF), degrade it the
way a real fax/photocopy is (grayscale, skew, sensor grain, blur), and re-emit an
**image-only** PDF (Pillow). The result carries no text layer, so ``extract_pdf``
routes it to vision — and the model's reading still faces the identical
arithmetic gate the 835 does.

``smudge_text`` optionally obscures exactly one money cell so a faithful vision
read returns a low per-line confidence — which the 0.90 floor turns into a
``low_confidence_extraction`` exception that fails closed to a human. That is the
demo's money shot: the system declines to *guess* at a number it cannot read,
instead of inventing one.

Determinism: every effect draws from a single seeded RNG, and the underlying
numbers come from the seeded generator — same seed → same fixture.

    python -m gen.eob_scan --out fixtures/scanned_eob.pdf
"""

from __future__ import annotations

import os
import random
import tempfile
from datetime import date
from pathlib import Path

import fitz  # PyMuPDF — rasterize a digital PDF without poppler/system deps
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter

from app.models import Remittance
from gen.adjudicator import adjudicate_claim
from gen.builder import assemble
from gen.catalog import PAYERS
from gen.claim_factory import RatesConfig, build_claims
from gen.eob_pdf import render_eob_pdf

PAID_DATE = date(2026, 6, 1)
PAPER = 245  # off-white "scan background" grey


def _noise_layer(size, rng):
    """A cheap, seeded fax-grain texture: low-res random noise upscaled."""
    w, h = size
    sw, sh = max(1, w // 8), max(1, h // 8)
    small = Image.new("L", (sw, sh))
    small.putdata([rng.randint(0, 255) for _ in range(sw * sh)])
    return small.resize((w, h), Image.BILINEAR)


def _smudge(img, box, rng):
    """Make one money cell illegible — heavy local blur plus dark ink blotches."""
    pad = max(2, int((box[3] - box[1]) / 3))
    x0 = max(0, int(box[0]) - pad)
    y0 = max(0, int(box[1]) - pad)
    x1 = min(img.width, int(box[2]) + pad)
    y1 = min(img.height, int(box[3]) + pad)
    region = img.crop((x0, y0, x1, y1))
    w, h = region.size
    radius = max(3, h // 2)
    region = region.filter(ImageFilter.GaussianBlur(radius))

    blot = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(blot)
    for _ in range(3):
        cx, cy = rng.randint(0, w), rng.randint(0, h)
        r = rng.randint(max(2, h // 2), max(3, h))
        draw.ellipse((cx - r, cy - r // 2, cx + r, cy + r // 2), fill=rng.randint(40, 110))
    blot = blot.filter(ImageFilter.GaussianBlur(radius))
    region = ImageChops.darker(region, blot)
    img.paste(region, (x0, y0))


def _degrade(img, rng):
    """Global photocopy/fax look: grain, softening, dulled contrast, slight skew."""
    img = img.convert("L")
    img = Image.blend(img, _noise_layer(img.size, rng), 0.06)
    img = img.filter(ImageFilter.GaussianBlur(0.5))
    img = ImageEnhance.Contrast(img).enhance(0.85)
    img = ImageEnhance.Brightness(img).enhance(1.03)
    angle = rng.uniform(-1.2, 1.2)
    return img.rotate(angle, resample=Image.BILINEAR, fillcolor=PAPER, expand=False)


def render_scanned_eob(remit: Remittance, path: str, *, smudge_text: str | None = None,
                       dpi: int = 150, seed: int = 7) -> None:
    """Render ``remit`` to a degraded, image-only PDF at ``path`` (no text layer)."""
    rng = random.Random(seed)
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.close()
    try:
        render_eob_pdf(remit, tmp.name)
        doc = fitz.open(tmp.name)
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pages, smudged = [], False
        for page in doc:
            boxes = []
            if smudge_text and not smudged:
                hits = page.search_for(smudge_text)
                if hits:
                    r = hits[0]
                    boxes.append((r.x0 * zoom, r.y0 * zoom, r.x1 * zoom, r.y1 * zoom))
                    smudged = True
            pix = page.get_pixmap(matrix=mat)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
            for box in boxes:
                _smudge(img, box, rng)
            pages.append(_degrade(img, rng))
        doc.close()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        pages[0].save(path, "PDF", save_all=True, append_images=pages[1:], resolution=float(dpi))
    finally:
        os.unlink(tmp.name)


def build_scan_sample(out_path: str, *, seed: int = 7, claims: int = 3,
                      dpi: int = 150, smudge: bool = True):
    """Generate a deterministic remittance and render it as a scanned EOB fixture.

    Returns ``(remittance, smudge_text)``. The smudged cell is the first paid
    amount that actually moved money — the one whose illegibility should fail
    closed rather than be guessed.
    """
    rng = random.Random(seed)
    payer = rng.choice(PAYERS)
    raw = build_claims(rng, claims, RatesConfig(denial=0.34), PAID_DATE)
    adjudicated = [adjudicate_claim(rng, rc, payer) for rc in raw]
    remit = assemble(adjudicated, payer, PAID_DATE)[0]
    remit.assert_valid()

    smudge_text = None
    if smudge:
        target = next((ln for c in remit.claims for ln in c.lines if ln.paid > 0),
                      remit.claims[0].lines[0])
        smudge_text = f"{target.paid:.2f}"

    render_scanned_eob(remit, out_path, smudge_text=smudge_text, dpi=dpi, seed=seed)
    return remit, smudge_text


def main(argv=None) -> int:
    import argparse

    from app.extract.text_layer import has_text_layer

    p = argparse.ArgumentParser(
        prog="gen.eob_scan",
        description="Synthesize a degraded, image-only scanned EOB (exercises the vision path).")
    p.add_argument("--out", default="fixtures/scanned_eob.pdf")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--claims", type=int, default=3)
    p.add_argument("--dpi", type=int, default=150)
    p.add_argument("--no-smudge", action="store_true", help="do not obscure a money cell")
    args = p.parse_args(argv)

    remit, smudge_text = build_scan_sample(
        args.out, seed=args.seed, claims=args.claims, dpi=args.dpi, smudge=not args.no_smudge)
    routed = "VISION" if not has_text_layer(args.out) else "TEXT"
    print(f"✓ wrote {args.out}")
    print(f"  payer={remit.payer}  claims={len(remit.claims)}  trn={remit.trn}  eft=${remit.eft_amount:.2f}")
    print(f"  smudged paid cell = {smudge_text}" if smudge_text else "  (no smudge)")
    print(f"  has_text_layer=False  → routes to {routed} path")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
