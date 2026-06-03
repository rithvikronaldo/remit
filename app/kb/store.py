"""Vector store for the knowledge base.

Two backends behind one interface:
  - ``InMemoryStore`` (default): pure-Python cosine over the (small) corpus. No
    Postgres, no service, no cost — used for dev, tests, and the demo.
  - ``PgVectorStore``: the production target (pgvector + HNSW), used when a
    DATABASE_URL is configured. Same interface so ``retrieve()`` is backend-agnostic.

Embeddings are stored L2-normalized, so cosine similarity is a plain dot product.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass
class KnowledgeChunk:
    id: str
    chunk_type: str                       # carc | rarc | group_code | payer_rule | playbook
    content: str
    code: Optional[str] = None
    group_applicable: Optional[list[str]] = None
    payer: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    embedding: Optional[list[float]] = None


class VectorStore(Protocol):
    def add(self, chunks: list[KnowledgeChunk]) -> None: ...
    def by_code(self, code: str, chunk_types: tuple[str, ...]) -> list[KnowledgeChunk]: ...
    def by_group(self, group: str, chunk_types: tuple[str, ...]) -> list[KnowledgeChunk]: ...
    def semantic(self, query_vec: list[float], chunk_types: tuple[str, ...],
                 payer: Optional[str], k: int) -> list[tuple[KnowledgeChunk, float]]: ...
    def all(self) -> list[KnowledgeChunk]: ...


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class InMemoryStore:
    def __init__(self) -> None:
        self._chunks: list[KnowledgeChunk] = []
        self._by_id: dict[str, KnowledgeChunk] = {}

    def add(self, chunks: list[KnowledgeChunk]) -> None:
        for c in chunks:
            if c.id in self._by_id:
                continue
            self._chunks.append(c)
            self._by_id[c.id] = c

    def by_code(self, code: str, chunk_types: tuple[str, ...]) -> list[KnowledgeChunk]:
        return [c for c in self._chunks if c.code == code and c.chunk_type in chunk_types]

    def by_group(self, group: str, chunk_types: tuple[str, ...]) -> list[KnowledgeChunk]:
        return [
            c for c in self._chunks
            if c.chunk_type in chunk_types and (c.group_applicable or []) and group in c.group_applicable
        ]

    def semantic(self, query_vec, chunk_types, payer, k):
        scored: list[tuple[KnowledgeChunk, float]] = []
        for c in self._chunks:
            if c.chunk_type not in chunk_types or c.embedding is None:
                continue
            # Payer filter: payer-specific chunk must match, or be payer-agnostic (null).
            if c.payer is not None and payer is not None and c.payer != payer:
                continue
            scored.append((c, _dot(query_vec, c.embedding)))
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:k]

    def all(self) -> list[KnowledgeChunk]:
        return list(self._chunks)


class PgVectorStore:
    """pgvector-backed store (production target). Requires psycopg + pgvector and a
    populated ``knowledge_chunk`` table. Implemented lazily so importing this module
    never requires a database driver."""

    def __init__(self, dsn: str):
        import psycopg  # lazy
        self._conn = psycopg.connect(dsn)

    def add(self, chunks: list[KnowledgeChunk]) -> None:
        raise NotImplementedError("Corpus loading into pgvector is done by app/kb/build.py --pg")

    def by_code(self, code, chunk_types):
        rows = self._conn.execute(
            "SELECT id, chunk_type, content, code, group_applicable, payer, metadata "
            "FROM knowledge_chunk WHERE code = %s AND chunk_type = ANY(%s)",
            [code, list(chunk_types)],
        ).fetchall()
        return [self._row(r) for r in rows]

    def by_group(self, group, chunk_types):
        rows = self._conn.execute(
            "SELECT id, chunk_type, content, code, group_applicable, payer, metadata "
            "FROM knowledge_chunk WHERE %s = ANY(group_applicable) AND chunk_type = ANY(%s)",
            [group, list(chunk_types)],
        ).fetchall()
        return [self._row(r) for r in rows]

    def semantic(self, query_vec, chunk_types, payer, k):
        rows = self._conn.execute(
            "SELECT id, chunk_type, content, code, group_applicable, payer, metadata, "
            "1 - (embedding <=> %s::vector) AS score "
            "FROM knowledge_chunk "
            "WHERE chunk_type = ANY(%s) AND (payer = %s OR payer IS NULL) "
            "ORDER BY embedding <=> %s::vector LIMIT %s",
            [query_vec, list(chunk_types), payer, query_vec, k],
        ).fetchall()
        return [(self._row(r), float(r[7])) for r in rows]

    def all(self):
        rows = self._conn.execute(
            "SELECT id, chunk_type, content, code, group_applicable, payer, metadata "
            "FROM knowledge_chunk"
        ).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(r) -> KnowledgeChunk:
        return KnowledgeChunk(
            id=r[0], chunk_type=r[1], content=r[2], code=r[3],
            group_applicable=r[4], payer=r[5], metadata=r[6] or {},
        )
