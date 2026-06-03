"""Decision cache — the dominant cost saver.

Keyed by ``(group_code, reason_code, payer, cdt_code, corpus_version,
prompt_version)`` (NOT amount — the action doesn't depend on it). Each unique
adjudication situation calls the LLM at most once, ever; re-runs and re-evals are
free. Disk-backed (JSON) so the saving survives across processes, which is what
makes the iterate-on-prompt/corpus loop cheap.

Stores the *raw* LLM ``Decision`` (pre-guardrail) so guardrail logic can change
without invalidating the cache. Pass ``path=None`` for an in-memory cache (tests).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional


def make_key(group_code: str, reason_code: str, payer: Optional[str], cdt_code: Optional[str],
             corpus_version: str, prompt_version: str) -> str:
    raw = "|".join([group_code, reason_code, payer or "", cdt_code or "",
                    corpus_version, prompt_version])
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


class DecisionCache:
    def __init__(self, path: Optional[str] = ".cache/decisions.json"):
        self.path = Path(path) if path else None
        self._data: dict[str, dict] = {}
        if self.path and self.path.exists():
            try:
                self._data = json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def get(self, key: str) -> Optional[dict]:
        return self._data.get(key)

    def set(self, key: str, value: dict) -> None:
        self._data[key] = value
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, indent=2))

    def __len__(self) -> int:
        return len(self._data)
