"""Phase 0 CLI.

    python -m gen --seed 42 --claims 20 --denial-rate 0.1 --pdf --out fixtures/run-42

Emits, into ``--out``:
  remit-001.835[, remit-002.835]   the X12 835 file(s) (split → a second file)
  eob-001.pdf[, ...]               human-readable EOB PDFs (with --pdf)
  golden.json                      the ground-truth oracle
  manifest.json                    run parameters + file list

Everything is seeded — same flags + seed → byte-identical output.
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import date
from decimal import Decimal
from pathlib import Path

from gen.adjudicator import adjudicate_claim
from gen.builder import assemble
from gen.catalog import PAYERS
from gen.claim_factory import RatesConfig, build_claims
from gen.golden import build_golden
from gen.x12_writer import write_835

# Fixed reference date for reproducibility (paid_date), overridable via --paid-date.
DEFAULT_PAID_DATE = date(2026, 6, 1)


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="gen", description="Remit Phase 0 synthetic data generator")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--claims", type=int, default=20)
    p.add_argument("--denial-rate", type=float, default=0.0)
    p.add_argument("--reversal-rate", type=float, default=0.0)
    p.add_argument("--cob-rate", type=float, default=0.0)
    p.add_argument("--overpayment-rate", type=float, default=0.0)
    p.add_argument("--split-rate", type=float, default=0.0)
    p.add_argument("--underpayment-rate", type=float, default=0.0,
                   help="fraction of claims the payer underpays below the contracted rate")
    p.add_argument("--plb", action="store_true", help="add a provider-level adjustment to the primary remittance")
    p.add_argument("--pdf", action="store_true", help="also render EOB PDFs (requires reportlab)")
    p.add_argument("--paid-date", type=date.fromisoformat, default=DEFAULT_PAID_DATE)
    p.add_argument("--out", type=str, default="fixtures/run-42")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    rng = random.Random(args.seed)

    payer = rng.choice(PAYERS)
    rates = RatesConfig(
        denial=args.denial_rate, reversal=args.reversal_rate, cob=args.cob_rate,
        overpayment=args.overpayment_rate, split=args.split_rate,
        underpayment=args.underpayment_rate,
    )
    raw_claims = build_claims(rng, args.claims, rates, args.paid_date)
    adjudicated = [adjudicate_claim(rng, rc, payer) for rc in raw_claims]

    plb_amount = Decimal("-25.00") if args.plb else Decimal("0.00")
    remittances = assemble(adjudicated, payer, args.paid_date, plb_amount)

    # Invalid fixtures must be impossible by construction.
    for r in remittances:
        r.assert_valid()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    files: list[str] = []

    for i, remit in enumerate(remittances, start=1):
        x12_path = out / f"remit-{i:03d}.835"
        x12_path.write_text(write_835(remit))
        files.append(x12_path.name)
        if args.pdf:
            from gen.eob_pdf import render_eob_pdf
            pdf_path = out / f"eob-{i:03d}.pdf"
            render_eob_pdf(remit, str(pdf_path))
            files.append(pdf_path.name)

    golden = build_golden(adjudicated, remittances, args.seed)
    (out / "golden.json").write_text(json.dumps(golden, indent=2))
    files.append("golden.json")

    manifest = {
        "seed": args.seed, "claims": args.claims, "payer": payer,
        "paid_date": args.paid_date.isoformat(),
        "rates": vars(rates), "plb": args.plb, "pdf": args.pdf,
        "remittances": [{"trn": r.trn, "eft_amount": f"{r.eft_amount:.2f}",
                         "claims": len(r.claims)} for r in remittances],
        "files": files,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))

    role_counts: dict[str, int] = {}
    for ac in adjudicated:
        role_counts[ac.role] = role_counts.get(ac.role, 0) + 1
    print(f"✓ wrote {len(files)} files to {out}/")
    print(f"  payer={payer}  claims={args.claims}  remittances={len(remittances)}")
    print(f"  roles={role_counts}")
    print(f"  total EFT={sum(r.eft_amount for r in remittances):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
