"""Phase 7 — reconciliation tests."""

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
from app.reconcile import commit_if_reconciled, reconcile
from app.settle import SettlementStore, settle_remittance
from gen.adjudicator import adjudicate_claim
from gen.builder import assemble
from gen.catalog import PAYERS
from gen.claim_factory import RatesConfig, build_claims

PAID_DATE = date(2026, 6, 1)
STORE = build_store(embedder=HashEmbedder())


def _remits(seed, claims, rates, plb=Decimal("0.00")):
    rng = random.Random(seed)
    payer = rng.choice(PAYERS)
    raw = build_claims(rng, claims, rates, PAID_DATE)
    adjudicated = [adjudicate_claim(rng, rc, payer) for rc in raw]
    return assemble(adjudicated, payer, PAID_DATE, plb)


def _settle(remit):
    return settle_remittance(remit, store=STORE, chain=MetadataStubChain(), cache=DecisionCache(path=None))


def test_clean_remittance_reconciles_and_commits():
    store = SettlementStore()
    for remit in _remits(7, 15, RatesConfig()):
        recon, written = commit_if_reconciled(store, remit, _settle(remit))
        assert recon.tied and not recon.held
        assert recon.delta == money(0)
        assert written > 0
    assert len(store) > 0


def test_plb_included_in_tie_out():
    for remit in _remits(42, 12, RatesConfig(), plb=Decimal("-25.00")):
        recon = reconcile(remit, _settle(remit))
        assert recon.tied, recon.exceptions
        assert recon.plb_amount == money("-25.00")


def test_injected_eft_imbalance_is_held():
    store = SettlementStore()
    remit = _remits(7, 8, RatesConfig())[0]
    remit.eft_amount = money(remit.eft_amount + Decimal("0.01"))  # one cent off
    recon, written = commit_if_reconciled(store, remit, _settle(remit))
    assert not recon.tied
    assert any(e["reason"] == "reconciliation_break" for e in recon.exceptions)
    assert recon.held and written == 0      # held, not partially committed
    assert len(store) == 0


def test_dropped_settlement_line_breaks_reconciliation():
    remit = _remits(7, 8, RatesConfig())[0]
    settlement = _settle(remit)
    settlement.records.pop()                # simulate a lost line
    recon = reconcile(remit, settlement)
    assert not recon.tied
    assert recon.delta != money(0)


def test_overpayment_ties_eft_but_is_held():
    """The money moved (EFT ties), but the flagged line holds the remittance."""
    remit = next(r for r in _remits(3, 10, RatesConfig(overpayment=1.0)) if r.claims)
    settlement = _settle(remit)
    recon = reconcile(remit, settlement)
    assert recon.delta == money(0)          # EFT still balances — money did transfer
    assert recon.held                       # but held: a line needs review
    assert settlement.exceptions


def test_split_remittances_each_reconcile_independently():
    remits = _remits(42, 20, RatesConfig(split=0.3))
    assert len(remits) == 2
    for remit in remits:
        recon = reconcile(remit, _settle(remit))
        assert recon.tied, (remit.trn, recon.exceptions)
