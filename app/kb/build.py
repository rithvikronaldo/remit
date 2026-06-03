"""Load the corpus source docs, embed them, and populate a vector store.

For dev/tests/demo this builds an in-memory store (cached per process). The same
chunks can be loaded into pgvector for production via ``python -m app.kb.build --pg``.
Competence is extended by adding documents under ``corpus/`` — not by editing code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from app.kb.embeddings import Embedder, get_embedder
from app.kb.store import InMemoryStore, KnowledgeChunk, VectorStore

CORPUS_DIR = Path(__file__).resolve().parents[2] / "corpus"
CORPUS_FILES = ["group_codes.json", "carc.json", "rarc.json", "payer_rules.json", "playbook.json"]

# Bump when corpus content changes — part of the decision cache key (Phase 2).
CORPUS_VERSION = "v1"


def load_chunks(corpus_dir: Path = CORPUS_DIR) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    for fname in CORPUS_FILES:
        path = corpus_dir / fname
        if not path.exists():
            continue
        for raw in json.loads(path.read_text()):
            chunks.append(KnowledgeChunk(
                id=raw["id"],
                chunk_type=raw["chunk_type"],
                content=raw["content"],
                code=raw.get("code"),
                group_applicable=raw.get("group_applicable"),
                payer=raw.get("payer"),
                metadata=raw.get("metadata") or {},
            ))
    return chunks


def build_store(embedder: Optional[Embedder] = None,
                store: Optional[VectorStore] = None,
                corpus_dir: Path = CORPUS_DIR) -> VectorStore:
    embedder = embedder or get_embedder()
    store = store or InMemoryStore()
    chunks = load_chunks(corpus_dir)
    vectors = embedder.embed([c.content for c in chunks])
    for chunk, vec in zip(chunks, vectors):
        chunk.embedding = vec
    store.add(chunks)
    return store


_default: Optional[VectorStore] = None


def default_store() -> VectorStore:
    """Process-cached in-memory store, built once on first use."""
    global _default
    if _default is None:
        _default = build_store()
    return _default


if __name__ == "__main__":
    # Quick corpus summary / smoke build.
    s = build_store()
    chunks = s.all()
    by_type: dict[str, int] = {}
    for c in chunks:
        by_type[c.chunk_type] = by_type.get(c.chunk_type, 0) + 1
    print(f"Loaded {len(chunks)} chunks (corpus {CORPUS_VERSION}): {by_type}")
    print(f"Embedder: {get_embedder().name}")
