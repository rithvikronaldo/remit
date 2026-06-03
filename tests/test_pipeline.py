"""Phase 9 — whole-pipeline eval + observability tests."""

from __future__ import annotations

import os

os.environ["REMIT_EMBEDDER"] = "hash"

import json
import tempfile
from pathlib import Path

from app.observability import write_run_report
from eval.pipeline_eval import meets_targets, run_pipeline_eval
from gen.cli import main as gen_main


def _make_fixture(tmp: str, seed=5, claims=10):
    gen_main(["--seed", str(seed), "--claims", str(claims), "--denial-rate", "0.1",
              "--cob-rate", "0.1", "--overpayment-rate", "0.05", "--split-rate", "0.1",
              "--plb", "--pdf", "--out", tmp])
    return tmp


def test_pipeline_eval_meets_targets_on_clean_fixture():
    with tempfile.TemporaryDirectory() as tmp:
        _make_fixture(tmp)
        report = run_pipeline_eval(tmp)
        assert report["settlement_accuracy"] == 1.0
        assert report["reconciliation_pass_rate"] == 1.0
        assert report["decision_accuracy"] >= 0.95
        assert report["extraction_accuracy"] == 1.0
        assert report["meets_targets"]


def test_report_is_written_json_and_html():
    with tempfile.TemporaryDirectory() as tmp:
        _make_fixture(tmp)
        report = run_pipeline_eval(tmp)
        paths = write_run_report(report, out_dir=tmp, stem="report")
        assert Path(paths["json"]).exists() and Path(paths["html"]).exists()
        assert "Regression gate" in Path(paths["html"]).read_text()
        assert json.loads(Path(paths["json"]).read_text())["fixture"] == tmp


def test_regression_gate_trips_on_eft_imbalance():
    """Tamper an 835's EFT → reconciliation fails → gate goes red."""
    with tempfile.TemporaryDirectory() as tmp:
        _make_fixture(tmp)
        edi = sorted(Path(tmp).glob("remit-*.835"))[0]
        segs = edi.read_text().split("~")
        for i, s in enumerate(segs):
            if s.strip().startswith("BPR*"):
                parts = s.strip().split("*")
                parts[2] = "999999.99"          # corrupt the EFT total
                segs[i] = "*".join(parts)
                break
        edi.write_text("~".join(segs))
        report = run_pipeline_eval(tmp)
        assert report["reconciliation_pass_rate"] < 1.0
        assert not report["meets_targets"]


def test_meets_targets_skips_inapplicable_metrics():
    # extraction_accuracy None (no PDFs) must not fail the gate.
    report = {"decision_accuracy": 1.0, "denial_action_accuracy": 1.0,
              "settlement_accuracy": 1.0, "reconciliation_pass_rate": 1.0,
              "extraction_accuracy": None}
    assert meets_targets(report)
