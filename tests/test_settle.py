"""Phase 6 — settlement tests.

The headline metric is settlement accuracy: each line's settled buckets + status
must equal the golden's expected_settlement / expected_status. Runs free with the
hash embedder + metadata stub chain.
"""

from __future__ import annotations

import os

os.environ["REMIT_EMBEDDER"] = "hash"

import random
from datetime import date
from decimal import Decimal

from app.decide.cache import DecisionCache
from app.decide.chain import MetadataStubChain
from app.kb.build import build_store
from app.kb.embeddings import HashEmbedder
from app.models import money
from app.settle import SettlementStore, settle_remittance
from gen.adjudicator import adjudicate_claim
from gen.builder import assemble
from gen.catalog import PAYERS
from gen.claim_factory import RatesConfig, build_claims
from gen.golden import build_golden

PAID_DATE = date(2026, 6, 1)
STORE = build_store(embedder=HashEmbedder())


def _setup(seed, claims, rates, plb=Decimal("0.00")):
    rng = random.Random(seed)
    payer = rng.choice(PAYERS)
    raw = build_claims(rng, claims, rates, PAID_DATE)
    adjudicated = [adjudicate_claim(rng, rc, payer) for rc in raw]
    remits = assemble(adjudicated, payer, PAID_DATE, plb)
    golden = build_golden(adjudicated, remits, seed)
    return remits, golden


def _settle_all(remits):
    cache = DecisionCache(path=None)
    chain = MetadataStubChain()
    out = []
    for r in remits:
        out.append(settle_remittance(r, store=STORE, chain=chain, cache=cache))
    return out


def test_every_settled_line_fully_accounts_for_billed():
    remits, _ = _setup(42, 30, RatesConfig(denial=0.1, reversal=0.05, cob=0.1,
                                           overpayment=0.05, split=0.1), plb=Decimal("-25.00"))
    for res in _settle_all(remits):
        for rec in res.records:
            if rec.status == "queued":
                # queued lines (e.g. overpayment) are flagged; the unaccounted
                # residual is exactly what needs a human, so they carry exceptions.
                assert rec.exceptions
            else:
                assert rec.fully_accounted(), (rec.claim_line_id, rec.status)


def test_settlement_matches_golden_buckets_and_status():
    remits, golden = _setup(42, 30, RatesConfig(denial=0.1, reversal=0.05, cob=0.1,
                                                overpayment=0.05, split=0.1))
    # Index settlement records by (claim_id, cdt) accumulating across split remittances.
    recs: dict[tuple, list] = {}
    for res in _settle_all(remits):
        for rec in res.records:
            recs.setdefault((rec.claim_id, rec.cdt_code), []).append(rec)

    total = correct = 0
    for claim in golden["claims"]:
        for line in claim["lines"]:
            key = (claim["claim_id"], line["cdt_code"])
            candidates = recs.get(key)
            assert candidates, f"no settlement for {key}"
            rec = candidates.pop(0)
            exp = line["expected_settlement"]
            total += 1
            buckets_ok = (
                rec.insurance_paid == money(exp["insurance_paid"]) and
                rec.contractual_writeoff == money(exp["contractual_writeoff"]) and
                rec.patient_responsibility == money(exp["patient_responsibility"]) and
                rec.secondary_responsibility == money(exp["secondary_responsibility"])
            )
            status_ok = rec.status == line["expected_status"]
            correct += buckets_ok and status_ok
            assert buckets_ok, (key, vars(rec), exp)
            assert status_ok, (key, rec.status, line["expected_status"])
    assert total > 0
    assert correct / total == 1.0   # settlement accuracy = 100% vs golden


def test_denial_dispositions():
    remits, _ = _setup(3, 12, RatesConfig(denial=1.0))
    statuses = set()
    for res in _settle_all(remits):
        for rec in res.records:
            statuses.add(rec.status)
            assert rec.insurance_paid == money(0)  # denials pay nothing
    assert statuses <= {"appealed", "settled"}      # appeal vs timely-filing writeoff


def test_idempotent_replay():
    remits, _ = _setup(42, 15, RatesConfig(denial=0.1, cob=0.1))
    store = SettlementStore()
    results = _settle_all(remits)
    first = sum(store.commit(r) for r in results)
    second = sum(store.commit(r) for r in results)   # replay
    assert first > 0
    assert second == 0          # nothing re-written
    assert len(store) == first


def test_atomic_commit_blocks_unbalanced():
    remits, _ = _setup(42, 5, RatesConfig())
    res = _settle_all(remits)[0]
    # Tamper one record so it no longer balances → remittance must not commit.
    res.records[0].insurance_paid = money(res.records[0].insurance_paid + Decimal("1.00"))
    store = SettlementStore()
    assert store.commit(res) == 0
    assert len(store) == 0
