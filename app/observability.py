"""Lightweight observability — structured logging + a run report.

Every chain run is traceable via LangSmith automatically when
``LANGCHAIN_TRACING_V2=true`` and ``LANGCHAIN_API_KEY`` are set (langchain picks
these up; no code needed here). This module adds structured event logging and a
self-contained JSON + HTML run report so any pipeline/eval run is inspectable
without external services.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

_logger = logging.getLogger("remit")
if not _logger.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(_h)
    _logger.setLevel(os.getenv("REMIT_LOG_LEVEL", "INFO"))


def log_event(event: str, **fields) -> None:
    """Emit a single structured (JSON) log line."""
    _logger.info(json.dumps({"event": event, **fields}, default=str))


def langsmith_enabled() -> bool:
    return os.getenv("LANGCHAIN_TRACING_V2", "").lower() == "true" and bool(os.getenv("LANGCHAIN_API_KEY"))


def write_run_report(report: dict, out_dir: str = "eval", stem: str = "last_pipeline_report") -> dict:
    """Write report.json + a minimal report.html. Returns the paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"{stem}.json"
    html_path = out / f"{stem}.html"
    json_path.write_text(json.dumps(report, indent=2, default=str))
    html_path.write_text(_render_html(report))
    return {"json": str(json_path), "html": str(html_path)}


def _render_html(report: dict) -> str:
    metrics = {k: v for k, v in report.items() if isinstance(v, (int, float, str, bool))}
    rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in metrics.items())
    targets = report.get("targets", {})
    gate = "PASS" if report.get("meets_targets") else "FAIL"
    color = "#1a7f37" if report.get("meets_targets") else "#cf222e"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Remit run report</title>
<style>body{{font:14px/1.5 -apple-system,system-ui,sans-serif;margin:2rem;color:#1f2328}}
table{{border-collapse:collapse;margin:1rem 0}}td{{border:1px solid #d0d7de;padding:.4rem .8rem}}
.gate{{font-weight:700;color:{color}}}code{{background:#f6f8fa;padding:.1rem .3rem;border-radius:4px}}</style>
</head><body>
<h1>Remit — run report</h1>
<p class="gate">Regression gate: {gate}</p>
<p>Version: <code>{json.dumps(report.get("version", {}))}</code></p>
<table>{rows}</table>
<h3>Targets</h3><pre>{json.dumps(targets, indent=2)}</pre>
</body></html>"""
