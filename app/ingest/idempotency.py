"""Ingestion helpers — file-type detection and content-hash idempotency.

The content hash is the idempotency key: re-ingesting the same artifact must not
double-settle. A real deployment checks the hash against the ``remittance`` table
before processing; here we expose the primitives and an in-memory registry for
tests/demo.
"""

from __future__ import annotations

import hashlib
from typing import Literal

SourceType = Literal["835", "pdf", "unknown"]


def content_hash(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def detect_source_type(filename: str, data: bytes | str = b"") -> SourceType:
    name = filename.lower()
    if name.endswith(".835") or name.endswith(".edi") or name.endswith(".txt"):
        return "835"
    if name.endswith(".pdf"):
        return "pdf"
    head = data[:64] if isinstance(data, bytes) else data[:64].encode()
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.lstrip().startswith(b"ISA"):
        return "835"
    return "unknown"


class SeenRegistry:
    """In-memory idempotency registry (the DB does this in production)."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def is_duplicate(self, h: str) -> bool:
        return h in self._seen

    def register(self, h: str) -> bool:
        """Returns True if newly registered, False if it was already present."""
        if h in self._seen:
            return False
        self._seen.add(h)
        return True
