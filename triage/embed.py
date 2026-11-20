"""Embedders for runbook retrieval.

Two are provided, behind one small interface (`fit(texts)`, `embed(texts) -> unit-length rows`):

  LsaEmbedder     latent semantic analysis: TF-IDF over words and word pairs, reduced with a truncated
                  SVD. Needs numpy only, runs offline, and is fitted on the runbook text itself. It
                  learns which words occur together in *these* runbooks. It does not know English, so
                  a word that never appears in the runbooks contributes nothing.
  OllamaEmbedder  asks a local Ollama server for real embeddings (`POST /api/embed`). Needs a pulled
                  embedding model, for example `ollama pull nomic-embed-text`.

Choose with `make_embedder("lsa")` or `make_embedder("ollama:nomic-embed-text")`.
"""
from __future__ import annotations

import json
import math
import os
import re
import urllib.request
from collections import Counter
from typing import Dict, List, Sequence

import numpy as np

_STOP = set("the a an and or of to in on for is are was were be been it its this that with from by at as not no if then than so we our "
            "you your do does did has have had after before into out up down over under again once only own same too very can will".split())


def _tokens(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP and len(w) > 1]


def _stem(w: str) -> str:
    """Strip a few common endings so 'restarting', 'restarted' and 'restart' share a feature."""
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _features(text: str) -> List[str]:
    stems = [_stem(w) for w in _tokens(text)]
    return stems + [f"{a}_{b}" for a, b in zip(stems, stems[1:])]


def _unit(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.where(n == 0, 1.0, n)


class LsaEmbedder:
    name = "lsa"

    def __init__(self, dim: int = 24) -> None:
        self.dim = dim
        self.vocab: Dict[str, int] = {}
        self.idf: np.ndarray = np.zeros(0)
        self.proj: np.ndarray = np.zeros((0, 0))

    def _tfidf(self, texts: Sequence[str]) -> np.ndarray:
        m = np.zeros((len(texts), len(self.vocab)))
        for r, t in enumerate(texts):
            for f, c in Counter(_features(t)).items():
                j = self.vocab.get(f)
                if j is not None:
                    m[r, j] = (1 + math.log(c)) * self.idf[j]
        return _unit(m)

    def fit(self, texts: Sequence[str]) -> "LsaEmbedder":
        df: Counter = Counter()
        for t in texts:
            df.update(set(_features(t)))
        self.vocab = {f: i for i, f in enumerate(sorted(df))}
        n = len(texts)
        self.idf = np.array([math.log((1 + n) / (1 + df[f])) + 1 for f in sorted(df)])
        x = self._tfidf(texts)
        _, s, vt = np.linalg.svd(x, full_matrices=False)
        k = max(1, min(self.dim, len(s)))
        self.proj = vt[:k].T          # vocabulary -> k latent dimensions
        return self

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        if not self.vocab:
            raise RuntimeError("fit() the embedder before embed()")
        return _unit(self._tfidf(texts) @ self.proj)


class OllamaEmbedder:
    """Real embeddings from a local Ollama server. `fit` does nothing: the model is already trained."""

    def __init__(self, model: str = "nomic-embed-text", url: str | None = None, timeout: float = 60.0) -> None:
        self.model = model
        self.url = (url or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
        if not self.url.startswith("http"):
            self.url = "http://" + self.url
        self.timeout = timeout
        self.name = f"ollama:{model}"

    def fit(self, texts: Sequence[str]) -> "OllamaEmbedder":
        return self

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        req = urllib.request.Request(self.url + "/api/embed", data=json.dumps({"model": self.model, "input": list(texts)}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read())
        vecs = data.get("embeddings")
        if not vecs or len(vecs) != len(texts):
            raise RuntimeError(f"Ollama returned {0 if not vecs else len(vecs)} embedding(s) for {len(texts)} text(s)")
        return _unit(np.array(vecs, dtype=float))


def make_embedder(spec: str = "lsa"):
    kind, _, arg = spec.partition(":")
    if kind == "lsa":
        return LsaEmbedder(int(arg) if arg else 24)
    if kind == "ollama":
        return OllamaEmbedder(arg or "nomic-embed-text")
    raise ValueError(f"unknown embedder '{spec}' (use lsa, lsa:<dims> or ollama:<model>)")
