"""Compare runbook retrieval methods: BM25, dense (embeddings) and hybrid.

    python -m evals.retrieval_eval                       # offline LSA embedder
    python -m evals.retrieval_eval --embedder ollama:nomic-embed-text

Two query sets, each scored at the runbook level (a chunk counts for the runbook it belongs to):

  written   evals/retrieval_queries.yaml: 50 hand-written descriptions, 5 per runbook, half in
            everyday words. Run with the metadata filter on and off.
  incidents the 30 lab incidents; the query is the filtered log patterns, the filter uses the
            incident's own alert type and severity.

Metrics: recall@1 (right runbook ranked first), recall@3 and MRR (mean reciprocal rank of the right
runbook, 0 when it is not returned at all). Counts are shown next to the rates, because with 50 queries
one query is 2 points.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List

import yaml

from triage.context import filter_logs
from triage.embed import make_embedder
from triage.lab import dev_set, eval_set
from triage.rag import METHODS, RunbookIndex

ROOT = Path(__file__).resolve().parent.parent
QUERIES = ROOT / "evals" / "retrieval_queries.yaml"


def ranked_runbooks(index: RunbookIndex, query: str, alert: str, severity: str, method: str, use_filter: bool) -> List[str]:
    seen: List[str] = []
    for h in index.search(query, alert, severity, k=len(index.chunks), method=method, use_filter=use_filter):
        if h.chunk.runbook_id not in seen:
            seen.append(h.chunk.runbook_id)
    return seen


def score(cases: List[dict], index: RunbookIndex, method: str, use_filter: bool) -> dict:
    r1 = r3 = 0
    rr = 0.0
    misses = []
    for c in cases:
        ranking = ranked_runbooks(index, c["text"], c["alert"], c["severity"], method, use_filter)
        pos = ranking.index(c["runbook"]) + 1 if c["runbook"] in ranking else None
        r1 += pos == 1
        r3 += pos is not None and pos <= 3
        rr += 1 / pos if pos else 0.0
        if pos != 1:
            misses.append({"id": c["id"], "want": c["runbook"], "got": ranking[:3]})
    n = len(cases)
    return {"n": n, "recall@1": r1, "recall@3": r3, "mrr": round(rr / n, 3), "misses": misses}


def written_cases() -> List[dict]:
    return yaml.safe_load(QUERIES.read_text(encoding="utf-8"))["queries"]


def incident_cases() -> List[dict]:
    return [{"id": i.id, "runbook": i.truth_runbook, "alert": i.alert["name"], "severity": i.alert["severity"],
             "text": " ".join(p.example for p in filter_logs(i))} for i in dev_set() + eval_set()]


def run(embedder_spec: str) -> dict:
    emb = make_embedder(embedder_spec)
    index = RunbookIndex(embedder=emb)
    written = written_cases()
    sets: Dict[str, List[dict]] = {
        "written, filter on": written,
        "written, filter off": written,
        "written, everyday words only, filter off": [c for c in written if c["style"] == "plain"],
        "written, shared terms only, filter off": [c for c in written if c["style"] == "overlap"],
        "incidents, filter on": incident_cases(),
    }
    out: dict = {"embedder": emb.name, "chunks": len(index.chunks), "runbooks": len(index.runbooks), "results": {}}
    for name, cases in sets.items():
        use_filter = name.endswith("filter on")
        out["results"][name] = {m: score(cases, index, m, use_filter) for m in METHODS}
    return out


def to_markdown(res: dict) -> str:
    lines = [f"# Runbook retrieval: BM25 vs dense vs hybrid", "",
             f"Embedder: `{res['embedder']}` · {res['runbooks']} runbooks, {res['chunks']} step chunks. "
             "Scored per runbook. Counts are the number of queries whose right runbook was ranked first (R@1) or in the top 3 (R@3).", ""]
    for name, by_method in res["results"].items():
        n = next(iter(by_method.values()))["n"]
        lines += [f"## {name} (n={n})", "", "| Method | R@1 | R@3 | MRR |", "|---|---|---|---|"]
        for m in METHODS:
            r = by_method[m]
            lines.append(f"| {m} | {r['recall@1']}/{n} ({r['recall@1'] / n:.0%}) | {r['recall@3']}/{n} ({r['recall@3'] / n:.0%}) | {r['mrr']} |")
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="retrieval_eval")
    p.add_argument("--embedder", default="lsa", help="lsa, lsa:<dims> or ollama:<model>")
    p.add_argument("--out", help="write <out>.md and <out>.json (default evals/results/retrieval-<embedder>)")
    args = p.parse_args(argv)
    res = run(args.embedder)
    md = to_markdown(res)
    print(md)
    stem = Path(args.out) if args.out else ROOT / "evals" / "results" / ("retrieval-" + args.embedder.replace(":", "-").replace("/", "-"))
    stem.parent.mkdir(parents=True, exist_ok=True)
    stem.with_suffix(".md").write_text(md + "\n", encoding="utf-8")
    stem.with_suffix(".json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
