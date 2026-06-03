"""Hybrid retrieval — the grounding layer the decision engine reasons over.

Two tiers merged (a 2-char code like "45" is noise to vector-search, so we don't):

  Tier 1 — exact (deterministic anchors): the canonical CARC/RARC definition by
  ``reason_code``, the group-code semantics by ``group_code``, and the denial
  playbook entries whose ``metadata.codes`` include ``reason_code`` (playbook is
  code-anchored, so an exact match is more reliable and more precise than
  similarity — a small, documented enhancement over the spec's "playbook via
  semantic only", which in a topically-dense corpus pulled in irrelevant
  playbooks and wasted prompt tokens).

  Tier 2 — semantic (judgement): embed a natural-language query and vector-search
  the *payer rules* filtered by payer, above a similarity floor. Payer rules are
  the context the code alone can't carry; the playbook is handled deterministically
  in Tier 1.

If neither tier yields anything (unknown code, no rules) → escalate signal. No
grounding → no decision. The decision chain (Phase 2) consumes ``citations``
directly and rejects any decision citing an id not in this result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.kb.build import default_store
from app.kb.embeddings import get_embedder
from app.kb.store import KnowledgeChunk, VectorStore

DEFAULT_K = 5
# Tuned for bge-small on this topically-dense corpus: relevant payer rules score
# ~0.65-0.77, loosely-related ones ~0.6, so 0.55 keeps the former and drops noise.
# The hash fallback maxes out far lower, but grounding never depends on the
# semantic tier — the exact anchors carry it.
DEFAULT_FLOOR = 0.55

_ANCHOR_TYPES = ("carc", "rarc")
_GROUP_TYPES = ("group_code",)
_SEMANTIC_TYPES = ("payer_rule",)


@dataclass
class RetrievalResult:
    chunks: list[KnowledgeChunk] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    escalate: bool = False
    reason: Optional[str] = None

    @classmethod
    def escalated(cls, reason: str) -> "RetrievalResult":
        return cls(escalate=True, reason=reason)


def retrieve(
    group_code: str,
    reason_code: str,
    payer: Optional[str] = None,
    cdt_code: Optional[str] = None,
    *,
    store: Optional[VectorStore] = None,
    k: int = DEFAULT_K,
    floor: float = DEFAULT_FLOOR,
) -> RetrievalResult:
    store = store or default_store()
    chosen: dict[str, KnowledgeChunk] = {}
    scores: dict[str, float] = {}

    # --- Tier 1: deterministic anchors ---
    for c in store.by_code(reason_code, _ANCHOR_TYPES):
        chosen[c.id] = c
    for c in store.by_group(group_code, _GROUP_TYPES):
        chosen[c.id] = c
    # Playbook is code-anchored via metadata.codes (a playbook may cover several
    # codes, e.g. 22+109) — deterministic and precise, no similarity needed.
    for c in store.all():
        if c.chunk_type != "playbook":
            continue
        codes = c.metadata.get("codes") or ([c.code] if c.code else [])
        if reason_code in codes:
            chosen[c.id] = c

    # --- Tier 2: semantic payer rules + playbook ---
    query = (
        f"Adjustment group {group_code} reason code {reason_code} "
        f"on dental procedure {cdt_code or 'unknown'} from payer {payer or 'any'}. "
        f"What does it mean and what should the practice do?"
    )
    qvec = get_embedder().embed([query])[0]
    for chunk, score in store.semantic(qvec, _SEMANTIC_TYPES, payer, k):
        if score < floor:
            continue
        if chunk.id not in chosen:
            chosen[chunk.id] = chunk
        scores[chunk.id] = score

    if not chosen:
        return RetrievalResult.escalated("no_grounding")

    chunks = list(chosen.values())
    return RetrievalResult(
        chunks=chunks,
        citations=[c.id for c in chunks],
        scores=scores,
    )


def render_chunks(chunks: list[KnowledgeChunk]) -> str:
    """Render retrieved chunks for the decision prompt context (Phase 2)."""
    lines = []
    for c in chunks:
        lines.append(f"[{c.id}] {c.content}")
    return "\n\n".join(lines)
