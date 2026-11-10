"""Policy store: clause-ID'd markdown documents + BM25 retrieval.

Retrieval is lexical BM25 implemented in-file (no embedding service needed for a
small policy corpus, fully offline-deterministic). The `PolicyStore` interface
is the seam where an embedding retriever would plug in later.

Every chunk carries a stable clause ID (POL-...) — that ID is what the model
cites and what the citation validator checks against.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from pydantic import BaseModel

_POLICY_DIR = Path(__file__).parent / "policies"
_TOKEN_RE = re.compile(r"[a-z0-9%]+")
_HEADING_RE = re.compile(r"^(?P<id>[A-Z][A-Z0-9]+(?:-[A-Z0-9]+)+)\s+(?P<title>.+)$")


class PolicyChunk(BaseModel):
    clause_id: str
    title: str
    text: str


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _split_document(path: Path) -> list[PolicyChunk]:
    chunks: list[PolicyChunk] = []
    clause_id, title, body = None, "", []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            if clause_id:
                chunks.append(
                    PolicyChunk(clause_id=clause_id, title=title, text="\n".join(body).strip())
                )
            m = _HEADING_RE.match(line[3:].strip())
            clause_id = m.group("id") if m else f"{path.stem.upper()}-{len(chunks) + 1:03d}"
            title = m.group("title") if m else line[3:].strip()
            body = []
        elif clause_id is not None:
            body.append(line)
    if clause_id:
        chunks.append(PolicyChunk(clause_id=clause_id, title=title, text="\n".join(body).strip()))
    return chunks


class PolicyStore:
    def __init__(self, chunks: list[PolicyChunk]) -> None:
        self.chunks = chunks
        self._doc_tokens = [_tokens(f"{c.clause_id} {c.title} {c.text}") for c in chunks]
        self._df: dict[str, int] = {}
        for toks in self._doc_tokens:
            for t in set(toks):
                self._df[t] = self._df.get(t, 0) + 1
        self._avgdl = sum(len(t) for t in self._doc_tokens) / max(len(self._doc_tokens), 1)

    @classmethod
    def from_directory(cls, directory: Path = _POLICY_DIR) -> PolicyStore:
        chunks: list[PolicyChunk] = []
        for path in sorted(directory.glob("*.md")):
            chunks.extend(_split_document(path))
        return cls(chunks)

    def get(self, clause_id: str) -> PolicyChunk | None:
        return next((c for c in self.chunks if c.clause_id == clause_id), None)

    def retrieve(self, query: str, top_k: int = 4) -> list[PolicyChunk]:
        """BM25 (k1=1.5, b=0.75) over clause chunks."""
        q_tokens = _tokens(query)
        n = len(self._doc_tokens)
        scores: list[float] = []
        k1, b = 1.5, 0.75
        for toks in self._doc_tokens:
            tf: dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            score = 0.0
            for qt in q_tokens:
                if qt not in tf:
                    continue
                df = self._df.get(qt, 0)
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                freq = tf[qt]
                denom = freq + k1 * (1 - b + b * len(toks) / self._avgdl)
                score += idf * freq * (k1 + 1) / denom
            scores.append(score)
        ranked = sorted(range(n), key=lambda i: scores[i], reverse=True)
        top = [i for i in ranked if scores[i] > 0][:top_k]
        return [self.chunks[i] for i in top]


def default_store() -> PolicyStore:
    return PolicyStore.from_directory()
