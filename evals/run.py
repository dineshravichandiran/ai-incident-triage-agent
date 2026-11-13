"""Evaluate the triage pipeline on the lab incidents: raw logs vs filtered context.

    python -m evals.run --backend rules --set eval
    python -m evals.run --backend ollama --model <name> --set eval --repeats 2
    TRIAGE_LLM_CMD="python my_adapter.py" python -m evals.run --backend command --set eval

For every incident it runs the pipeline twice, once with raw logs and once with the filtered context, and records:
  cause    the model's category equals the ground-truth cause
  runbook  the runbook the retrieval picked equals the ground-truth runbook
  grounded every log line the model quoted really appears in the context it was given
  tokens   estimated size of the context (about 4 characters per token) and, when the backend reports it, input tokens
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from triage.agent import triage
from triage.lab import dev_set, eval_set, manifest
from triage.llm import ANSWER_SCHEMA, SYSTEM_PROMPT, make_backend
from triage.rag import RunbookIndex

ROOT = Path(__file__).resolve().parent.parent


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip() or "uncommitted"
    except OSError:
        return "unknown"


def run_one(args):
    inc, mode, rep, llm, index = args
    res = triage(inc, llm, index, mode)
    return {
        "incident": inc.id, "mode": mode, "repeat": rep, "truth": inc.truth_cause, "alert": inc.alert["name"], "flags": inc.flags,
        "predicted": res.cause_category, "cause_correct": res.cause_category == inc.truth_cause,
        "runbook": res.runbook_id, "runbook_correct": res.runbook_id == inc.truth_runbook, "agree": res.agree,
        "confidence": res.confidence, "evidence": len(res.evidence), "evidence_grounded": sum(res.grounded),
        "context_tokens_est": res.context_tokens_est,
        "input_tokens_reported": res.usage.get("input_tokens"), "output_tokens_reported": res.usage.get("output_tokens"),
        "latency_s": round(res.latency_s, 2), "error": res.error,
    }


def measure_overhead(llm) -> int:
    """Input tokens the backend reports for an empty case: the fixed cost of the system prompt and the wrapper."""
    if llm.name == "rules-baseline":
        return 0
    return int(llm.complete_json(SYSTEM_PROMPT, "ALERT: none\n", ANSWER_SCHEMA).usage.get("input_tokens") or 0)


def summarise(rows: list, modes: list, overhead: int = 0) -> dict:
    out = {}
    for mode in modes:
        r = [x for x in rows if x["mode"] == mode]
        ev = sum(x["evidence"] for x in r)
        reported = [x["input_tokens_reported"] for x in r if x["input_tokens_reported"]]
        out[mode] = {
            "runs": len(r),
            "cause_correct": sum(x["cause_correct"] for x in r),
            "runbook_correct": sum(x["runbook_correct"] for x in r),
            "errors": sum(1 for x in r if x["error"]),
            "evidence_quoted": ev,
            "evidence_not_found_in_context": ev - sum(x["evidence_grounded"] for x in r),
            "mean_context_tokens_est": round(statistics.mean(x["context_tokens_est"] for x in r)),
            "mean_input_tokens_reported": round(statistics.mean(reported)) if reported else None,
            "mean_input_tokens_net_of_overhead": round(statistics.mean(reported)) - overhead if reported else None,
            "mean_latency_s": round(statistics.mean(x["latency_s"] for x in r), 2),
            "wrong_cause_ids": sorted({x["incident"] for x in r if not x["cause_correct"]}),
        }
    return out


def markdown(meta: dict, summary: dict) -> str:
    f, r = summary.get("filtered"), summary.get("raw")
    lines = [f"### {meta['label']}", "",
             f"Backend: `{meta['backend']}` · set: {meta['set']} ({meta['incidents']} incidents) · repeats: {meta['repeats']} · "
             f"{meta['date']} · code {meta['git_sha']}", "",
             "| Context | Cause correct | Right runbook | Quoted lines not in context | Mean input tokens (reported) | Same, minus fixed overhead | Mean latency |",
             "|---|---|---|---|---|---|---|"]
    for mode in ("raw", "filtered"):
        s = summary.get(mode)
        if not s:
            continue
        lines.append(f"| {mode} | {s['cause_correct']} / {s['runs']} | {s['runbook_correct']} / {s['runs']} | "
                     f"{s['evidence_not_found_in_context']} of {s['evidence_quoted']} | "
                     + (f"{s['mean_input_tokens_reported']:,} | {s['mean_input_tokens_net_of_overhead']:,}" if s['mean_input_tokens_reported'] is not None
                        else f"n/a (est. {s['mean_context_tokens_est']:,}) | n/a")
                     + f" | {s['mean_latency_s']}s |")
    if f and r:
        if f["mean_input_tokens_reported"] and r["mean_input_tokens_reported"]:
            cut = 100 * (1 - f["mean_input_tokens_reported"] / r["mean_input_tokens_reported"])
            cut_net = 100 * (1 - f["mean_input_tokens_net_of_overhead"] / r["mean_input_tokens_net_of_overhead"])
            lines += ["", f"Filtered input is {cut:.0f}% fewer tokens than raw as reported, {cut_net:.0f}% fewer once the fixed overhead "
                          f"({meta['overhead_input_tokens']:,} tokens per call) is taken out."]
        else:
            cut = 100 * (1 - f["mean_context_tokens_est"] / r["mean_context_tokens_est"])
            lines += ["", f"Filtered context is {cut:.0f}% smaller than raw (estimated tokens)."]
        if r["wrong_cause_ids"] or f["wrong_cause_ids"]:
            lines.append(f"Wrong cause: raw {r['wrong_cause_ids'] or 'none'}, filtered {f['wrong_cause_ids'] or 'none'}.")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--backend", default="rules", choices=["rules", "ollama", "command"])
    p.add_argument("--model")
    p.add_argument("--command")
    p.add_argument("--set", default="eval", choices=["dev", "eval"])
    p.add_argument("--modes", default="raw,filtered")
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--label")
    p.add_argument("--limit", type=int, help="only the first N incidents (for a quick check)")
    args = p.parse_args(argv)

    committed = json.loads((ROOT / "lab" / "manifest.json").read_text())
    if committed != manifest():
        print("lab data does not match lab/manifest.json: refusing to evaluate on changed data", file=sys.stderr)
        return 2

    incidents = (eval_set() if args.set == "eval" else dev_set())[: args.limit]
    modes = args.modes.split(",")
    llm = make_backend(args.backend, args.model, args.command)
    index = RunbookIndex()
    jobs = [(inc, m, rep, llm, index) for inc in incidents for m in modes for rep in range(args.repeats)]
    print(f"{len(jobs)} runs on {llm.name} ...", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(run_one, jobs))

    overhead = measure_overhead(llm)
    label = args.label or f"{args.backend}-{args.set}"
    meta = {"label": label, "backend": llm.name, "set": args.set, "incidents": len(incidents), "repeats": args.repeats,
            "modes": modes, "date": dt.date.today().isoformat(), "git_sha": _git_sha(), "overhead_input_tokens": overhead}
    summary = summarise(rows, modes, overhead)
    out_dir = ROOT / "evals" / "results"
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"{label}.json").write_text(json.dumps({"meta": meta, "summary": summary, "rows": rows}, indent=1))
    md = markdown(meta, summary)
    (out_dir / f"{label}.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
