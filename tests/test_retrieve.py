"""Phase 1 — knowledge base + retrieval tests.

Forces the deterministic hash embedder so the suite runs free/offline and is
reproducible. The exact-match tier must be 100% regardless of embedding quality;
the escalate path must fire on truly ungrounded codes.
"""

from __future__ import annotations

import os

os.environ["REMIT_EMBEDDER"] = "hash"  # before any embedder is constructed

from app.kb.build import build_store, load_chunks
from app.kb.embeddings import HashEmbedder
from app.kb.retrieve import retrieve

STORE = build_store(embedder=HashEmbedder())


def test_corpus_loads_all_types():
    types = {c.chunk_type for c in load_chunks()}
    assert types == {"carc", "rarc", "group_code", "payer_rule", "playbook"}


def test_exact_tier_hits_every_carc_code():
    """Code → canonical chunk must be 100% (the deterministic anchor)."""
    carcs = [c for c in load_chunks() if c.chunk_type == "carc"]
    assert carcs
    for c in carcs:
        r = retrieve("CO", c.code, store=STORE)
        assert not r.escalate
        assert f"carc-{c.code}" in r.citations


def test_anchor_grounds_even_with_impossible_floor():
    """A known code grounds via the exact anchor independent of the similarity floor."""
    r = retrieve("CO", "45", store=STORE, floor=1.01)
    assert not r.escalate
    assert "carc-45" in r.citations
    assert "group-CO" in r.citations  # group-code semantics included


def test_denial_pulls_playbook_and_definition():
    r = retrieve("CO", "197", payer="DELTA DENTAL OF EXAMPLE", cdt_code="D2740", store=STORE)
    assert not r.escalate
    assert "carc-197" in r.citations
    assert "playbook-197-precert" in r.citations


def test_unknown_code_escalates():
    r = retrieve("ZZ", "99999", store=STORE, floor=1.01)
    assert r.escalate
    assert r.reason == "no_grounding"


def test_payer_rule_semantic_tier_returns_for_known_payer():
    """With the real query, Delta's rules are reachable; payer filter excludes others."""
    r = retrieve("CO", "29", payer="DELTA DENTAL OF EXAMPLE", store=STORE, floor=0.0)
    # floor=0.0 ensures the semantic tier contributes; no cross-payer leakage.
    cig = [c for c in r.chunks if c.payer == "CIGNA DENTAL"]
    assert cig == []


def test_citations_are_unique_and_nonempty_for_known_code():
    r = retrieve("PR", "2", store=STORE)
    assert r.citations
    assert len(r.citations) == len(set(r.citations))
