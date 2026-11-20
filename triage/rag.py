"""Runbook retrieval: chunk by step, filter by metadata first, then search.

Each runbook step is one chunk, tagged with the runbook it belongs to, the alert
types and severities it applies to, and the step kind. A query first drops every
chunk whose metadata does not fit the alert, then ranks what is left. Three ways to rank:

  bm25    keyword scoring, no embeddings, no external service (the default)
  dense   cosine similarity between embeddings of the query and of each chunk
  hybrid  reciprocal rank fusion of the two rankings

`dense` and `hybrid` use `triage.embed` (offline LSA by default, or a local Ollama model).
Pick with `RunbookIndex(method=...)` or the `TRIAGE_RETRIEVAL` environment variable.
"""
from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml

RUNBOOK_DIR = Path(__file__).resolve().parent.parent / "runbooks"
_STOP = set("the a an and or of to in on for is are was were be been it its this that with from by at as not no if then than so we our "
            "you your do does did has have had after before into out up down over under again once only own same too very can will".split())


def tokenize(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP and len(w) > 1]


@dataclass
class Chunk:
    runbook_id: str
    step_id: str
    kind: str
    text: str
    alert_types: List[str]
    severities: List[str]
    search_text: str


@dataclass
class Hit:
    chunk: Chunk
    score: float


def load_runbooks(directory: Path = RUNBOOK_DIR) -> Dict[str, dict]:
    out = {}
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        out[data["id"]] = data
    return out


def chunk_runbooks(runbooks: Dict[str, dict]) -> List[Chunk]:
    chunks = []
    for rb in runbooks.values():
        for step in rb["steps"]:
            chunks.append(Chunk(
                runbook_id=rb["id"], step_id=step["id"], kind=step["kind"], text=step["text"],
                alert_types=rb["applies_to"]["alert_types"], severities=rb["applies_to"]["severities"],
                # The title and symptoms travel with every step so a step can be found by the problem it solves.
                search_text=f"{rb['title']}. {rb['symptoms']}. {step['text']}",
            ))
    return chunks


METHODS = ("bm25", "dense", "hybrid")
RRF_K = 60   # the usual constant for reciprocal rank fusion: score = sum of 1 / (RRF_K + rank)


class RunbookIndex:
    def __init__(self, runbooks: Optional[Dict[str, dict]] = None, k1: float = 1.5, b: float = 0.75,
                 method: Optional[str] = None, embedder=None) -> None:
        self.runbooks = runbooks or load_runbooks()
        self.chunks = chunk_runbooks(self.runbooks)
        self.k1, self.b = k1, b
        self.method = (method or os.environ.get("TRIAGE_RETRIEVAL") or "bm25").lower()
        if self.method not in METHODS:
            raise ValueError(f"unknown retrieval method '{self.method}' (use one of {', '.join(METHODS)})")
        self.embedder = embedder
        self._vectors = None
        self._docs = [Counter(tokenize(c.search_text)) for c in self.chunks]
        self._len = [sum(d.values()) for d in self._docs]
        self._avg = sum(self._len) / len(self._len)
        df: Counter = Counter()
        for d in self._docs:
            df.update(d.keys())
        n = len(self._docs)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def _score(self, i: int, terms: List[str]) -> float:
        d, dl = self._docs[i], self._len[i]
        s = 0.0
        for t in terms:
            f = d.get(t, 0)
            if f:
                s += self._idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self._avg))
        return s

    def filter(self, alert_type: str, severity: str) -> List[int]:
        return [i for i, c in enumerate(self.chunks) if alert_type in c.alert_types and severity in c.severities]

    def _dense_scores(self, query: str, candidates: List[int]) -> Dict[int, float]:
        if self._vectors is None:
            if self.embedder is None:
                from .embed import LsaEmbedder   # numpy is only needed for dense and hybrid
                self.embedder = LsaEmbedder()
            self.embedder.fit([c.search_text for c in self.chunks])
            self._vectors = self.embedder.embed([c.search_text for c in self.chunks])
        q = self.embedder.embed([query])[0]
        return {i: float(self._vectors[i] @ q) for i in candidates}

    def search(self, query: str, alert_type: str, severity: str, k: int = 3, method: Optional[str] = None,
               use_filter: bool = True) -> List[Hit]:
        """Top `k` chunks. `use_filter=False` ranks every chunk, ignoring alert type and severity."""
        method = (method or self.method).lower()
        candidates = self.filter(alert_type, severity) if use_filter else list(range(len(self.chunks)))
        terms = tokenize(query)
        bm25 = {i: self._score(i, terms) for i in candidates}
        if method == "bm25":
            scores = bm25
        else:
            dense = self._dense_scores(query, candidates)
            if method == "dense":
                scores = dense
            else:
                scores = {i: 0.0 for i in candidates}
                for ranking in ([i for i, v in sorted(bm25.items(), key=lambda kv: -kv[1]) if v > 0],
                                [i for i, v in sorted(dense.items(), key=lambda kv: -kv[1]) if v > 0]):
                    for rank, i in enumerate(ranking, start=1):
                        scores[i] += 1.0 / (RRF_K + rank)
        hits = [Hit(self.chunks[i], v) for i, v in scores.items()]
        hits.sort(key=lambda h: -h.score)
        return [h for h in hits[:k] if h.score > 0]

    def best_runbook(self, query: str, alert_type: str, severity: str) -> Optional[str]:
        """The runbook whose best-matching step scores highest, or None when nothing matches."""
        hits = self.search(query, alert_type, severity, k=1)
        return hits[0].chunk.runbook_id if hits else None
