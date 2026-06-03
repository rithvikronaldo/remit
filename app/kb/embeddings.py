"""Local, free embeddings for the knowledge base.

Primary backend: ``fastembed`` (ONNX, no torch) with BAAI/bge-small-en-v1.5
(384-dim) — runs locally, no per-query API cost. If fastembed isn't installed
(e.g. in CI or a minimal dev env) we fall back to a deterministic hashing
embedder so retrieval and tests still run, offline and free. The exact-match tier
of ``retrieve()`` does not depend on embedding quality, so the fallback keeps the
system correct (the semantic tier just gets weaker).

Set ``EMBEDDING_MODEL`` to override the fastembed model name. Set
``REMIT_EMBEDDER=hash`` to force the deterministic fallback.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Protocol

_DEFAULT_FASTEMBED_MODEL = "BAAI/bge-small-en-v1.5"
_FASTEMBED_DIM = 384
_HASH_DIM = 384
_TOKEN = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    dim: int
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec
    return [x / norm for x in vec]


class HashEmbedder:
    """Deterministic bag-of-tokens hashing embedder. Uses md5 (NOT Python's
    salted ``hash``) so vectors are identical across processes/runs."""

    def __init__(self, dim: int = _HASH_DIM):
        self.dim = dim
        self.name = f"hash-{dim}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for tok in _TOKEN.findall(text.lower()):
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                bucket = h % self.dim
                sign = 1.0 if (h >> 8) & 1 else -1.0
                vec[bucket] += sign
            out.append(_l2_normalize(vec))
        return out


class FastEmbedEmbedder:
    """Local ONNX embeddings via fastembed (lazy model load)."""

    def __init__(self, model_name: str = _DEFAULT_FASTEMBED_MODEL, dim: int = _FASTEMBED_DIM):
        from fastembed import TextEmbedding  # lazy: only when actually used

        self.name = model_name
        self.dim = dim
        self._model = TextEmbedding(model_name=model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        # fastembed already returns L2-normalized vectors for bge models.
        return [list(map(float, v)) for v in self._model.embed(texts)]


_cached: Embedder | None = None


def get_embedder() -> Embedder:
    """Return a process-cached embedder. Prefers fastembed; falls back to hash."""
    global _cached
    if _cached is not None:
        return _cached
    if os.getenv("REMIT_EMBEDDER", "").lower() == "hash":
        _cached = HashEmbedder()
        return _cached
    try:
        model = os.getenv("EMBEDDING_MODEL") or _DEFAULT_FASTEMBED_MODEL
        _cached = FastEmbedEmbedder(model_name=model)
    except Exception:
        _cached = HashEmbedder()
    return _cached
