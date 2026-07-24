"""Phase 10 — FastAPI surface tests (TestClient; no running server, no API key)."""

from __future__ import annotations

import os

os.environ["REMIT_EMBEDDER"] = "hash"

import pytest
from fastapi.testclient import TestClient

from app.api import app

client = TestClient(app)


def test_health_and_fixture_loaded():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["remittances"] >= 1   # fixtures/run-42 auto-loaded


def test_list_remittances_processed():
    rows = client.get("/remittances").json()
    assert rows and all(r["processed"] for r in rows)
    assert any(r["committed"] for r in rows)        # the clean split-remainder commits


def test_decisions_expose_citations_and_confidence():
    trn = client.get("/remittances").json()[0]["remittance_id"]
    decisions = client.get(f"/remittances/{trn}/decisions").json()
    assert decisions
    rag = [d for d in decisions if d["source"] == "rag"]
    assert rag, "expected some RAG decisions (denials)"
    for d in rag:
        assert d["citations"]          # every RAG decision is cited
        assert d["confidence"] is not None


def test_reconciliation_ties_to_the_cent():
    trn = client.get("/remittances").json()[0]["remittance_id"]
    recon = client.get(f"/remittances/{trn}/reconciliation").json()
    assert recon["delta"] == "0.00" and recon["tied"] is True


def test_exceptions_and_resolve_flow():
    items = client.get("/exceptions").json()
    assert items, "expected the overpayment line in the queue"
    item = items[0]
    out = client.post(f"/exceptions/{item['id']}/resolve",
                      json={"decision": "accept"}).json()
    assert out["settled"] is True
    # now resolved → no longer open
    assert all(i["id"] != item["id"] for i in client.get("/exceptions").json())


def test_resolve_override_requires_valid_action():
    # re-fetch an open item if any; otherwise this is vacuously fine
    items = client.get("/exceptions").json()
    if not items:
        pytest.skip("no open exceptions")
    bad = client.post(f"/exceptions/{items[0]['id']}/resolve",
                      json={"decision": "override", "action": "frobnicate"})
    assert bad.status_code == 400


def test_eval_run_returns_metrics():
    m = client.post("/eval/run").json()
    assert m["settlement_accuracy"] == 1.0
    assert m["reconciliation_pass_rate"] == 1.0
    assert m["meets_targets"] is True


def test_upload_and_duplicate_rejected():
    from pathlib import Path
    edi = sorted(Path("fixtures/run-42").glob("remit-*.835"))[0].read_bytes()
    # already ingested at startup → duplicate
    dup = client.post("/remittances", files={"file": ("remit-001.835", edi, "text/plain")})
    assert dup.status_code == 409


def test_sample_cohorts_load_as_distinct_remittances():
    """Every cohort has a unique TRN, so all three load alongside the startup
    fixture instead of silently colliding with it."""
    before = {r["remittance_id"] for r in client.get("/remittances").json()}
    trns = {sid: client.post(f"/samples/{sid}/load").json()["remittance_id"]
            for sid in ("clean", "denials", "underpayment")}
    assert len(set(trns.values())) == 3
    after = {r["remittance_id"] for r in client.get("/remittances").json()}
    assert set(trns.values()) <= after and before < after


def test_underpayment_sample_surfaces_revenue_at_risk():
    trn = client.post("/samples/underpayment/load").json()["remittance_id"]
    row = next(r for r in client.get("/remittances").json() if r["remittance_id"] == trn)
    assert float(row["revenue_at_risk"]) > 0
    assert row["tied"] is True and row["committed"] is False  # ties to the cent, held anyway

    stages = client.get(f"/remittances/{trn}/pipeline").json()["stages"]
    detect = next(s for s in stages if s["name"] == "Detect")
    assert detect["status"] == "warn" and "at risk" in detect["summary"]
    recon = next(s for s in stages if s["name"] == "Reconcile")
    assert recon["status"] == "done"                          # the money is perfect — that's the point


def test_underpayment_item_appeal_and_balance_bill_guardrail():
    client.post("/samples/underpayment/load")
    items = [i for i in client.get("/exceptions").json() if i["reason"] == "underpayment"]
    assert items, "expected underpaid lines in the inbox"
    exc = items[0]["evidence"]["line_exceptions"][0]
    assert float(exc["contracted_allowed"]) > float(exc["stated_allowed"])
    assert float(exc["recoverable"]) > 0
    # The shortfall is buried in a CO write-off → balance-billing stays blocked, even for a human.
    blocked = client.post(f"/exceptions/{items[0]['id']}/resolve",
                          json={"decision": "override", "action": "bill_patient"})
    assert blocked.status_code == 400
    # The actionable path: chase the payer for the money it withheld.
    ok = client.post(f"/exceptions/{items[0]['id']}/resolve",
                     json={"decision": "override", "action": "appeal"}).json()
    assert ok["action"] == "appeal" and ok["settled"] is True
