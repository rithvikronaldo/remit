"""Phase 1 — knowledge base + retrieval (the RAG corpus, the product's brain)."""

from app.kb.build import CORPUS_VERSION, build_store, default_store, load_chunks
from app.kb.retrieve import RetrievalResult, render_chunks, retrieve
from app.kb.store import InMemoryStore, KnowledgeChunk

__all__ = [
    "CORPUS_VERSION", "build_store", "default_store", "load_chunks",
    "RetrievalResult", "render_chunks", "retrieve",
    "InMemoryStore", "KnowledgeChunk",
]
