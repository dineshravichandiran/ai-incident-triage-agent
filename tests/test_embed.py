import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest

from evals.retrieval_eval import incident_cases, score, written_cases
from triage.embed import LsaEmbedder, OllamaEmbedder, make_embedder
from triage.rag import RunbookIndex


def test_lsa_rows_are_unit_length_and_similar_text_is_closer():
    texts = ["disk is full no space left", "filesystem out of space on disk", "tls certificate expired handshake failed"]
    emb = LsaEmbedder(dim=3).fit(texts)
    v = emb.embed(texts)
    assert np.allclose(np.linalg.norm(v, axis=1), 1.0)
    assert v[0] @ v[1] > v[0] @ v[2]


def test_lsa_needs_fit_first():
    with pytest.raises(RuntimeError):
        LsaEmbedder().embed(["x"])


def test_lsa_unknown_words_give_a_zero_vector_not_an_error():
    emb = LsaEmbedder(dim=3).fit(["disk is full", "tls certificate expired", "dns lookup failed"])
    assert np.allclose(emb.embed(["zzzz qqqq"]), 0.0)


def test_make_embedder_specs():
    assert make_embedder("lsa:8").dim == 8
    assert make_embedder("ollama:foo").model == "foo"
    with pytest.raises(ValueError):
        make_embedder("nope")


def test_dense_and_hybrid_find_the_runbook_by_meaning_of_shared_words():
    idx = RunbookIndex()
    for method in ("dense", "hybrid"):
        hits = idx.search("no space left on device and log rotation failing", "Disk Almost Full", "critical", method=method)
        assert hits and hits[0].chunk.runbook_id == "disk-full"


def test_default_method_is_bm25_and_unchanged(monkeypatch):
    monkeypatch.delenv("TRIAGE_RETRIEVAL", raising=False)
    assert RunbookIndex().method == "bm25"
    with pytest.raises(ValueError):
        RunbookIndex(method="magic")


def test_filter_off_searches_every_runbook():
    idx = RunbookIndex()
    on = {h.chunk.runbook_id for h in idx.search("restart the service", "Disk Almost Full", "critical", k=30)}
    off = {h.chunk.runbook_id for h in idx.search("restart the service", "Disk Almost Full", "critical", k=30, use_filter=False)}
    assert on <= {"disk-full"} and len(off) > 1


def test_hybrid_score_is_a_reciprocal_rank_sum():
    idx = RunbookIndex()
    top = idx.search("heap near its limit garbage collection", "High Memory Usage", "critical", method="hybrid", k=1)[0]
    assert 0 < top.score <= 2 / 61 + 1e-9      # two rankings, best possible rank 1 in each


def test_written_queries_are_valid_and_balanced():
    idx = RunbookIndex()
    cases = written_cases()
    assert len(cases) == 50
    per = {}
    for c in cases:
        rb = idx.runbooks[c["runbook"]]
        assert c["alert"] in rb["applies_to"]["alert_types"] and c["severity"] in rb["applies_to"]["severities"], c["id"]
        per[c["runbook"]] = per.get(c["runbook"], 0) + 1
    assert set(per.values()) == {5} and set(per) == set(idx.runbooks)


def test_score_counts_a_perfect_and_a_missing_query():
    idx = RunbookIndex()
    ok = {"id": "a", "runbook": "disk-full", "alert": "Disk Almost Full", "severity": "critical", "text": "no space left on device"}
    bad = {"id": "b", "runbook": "disk-full", "alert": "Disk Almost Full", "severity": "critical", "text": "zzzz qqqq"}
    r = score([ok, bad], idx, "bm25", True)
    assert (r["n"], r["recall@1"], r["recall@3"], r["mrr"]) == (2, 1, 1, 0.5)


def test_incident_cases_cover_the_lab_incidents():
    assert len(incident_cases()) == 30


class _Stub(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Stub.seen.append((self.path, body))
        vecs = [[float(len(t)), 1.0, 0.0] for t in body["input"]]
        out = json.dumps({"embeddings": vecs}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(out)

    def log_message(self, *a):
        pass


def test_ollama_embedder_posts_to_api_embed_and_normalises():
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        emb = OllamaEmbedder("m", url=f"http://127.0.0.1:{srv.server_port}")
        v = emb.embed(["abc", "abcdef"])
    finally:
        srv.shutdown()
    assert _Stub.seen[0][0] == "/api/embed" and _Stub.seen[0][1] == {"model": "m", "input": ["abc", "abcdef"]}
    assert v.shape == (2, 3) and np.allclose(np.linalg.norm(v, axis=1), 1.0)


def test_ollama_embedder_reports_a_bad_reply():
    class Bad(_Stub):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"embeddings": []}')
    srv = HTTPServer(("127.0.0.1", 0), Bad)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pytest.raises(RuntimeError):
            OllamaEmbedder("m", url=f"http://127.0.0.1:{srv.server_port}").embed(["x"])
    finally:
        srv.shutdown()
