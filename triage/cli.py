"""Command line: inject an incident, triage it, approve a fix, run the demo."""
from __future__ import annotations

import argparse
import json
import sys

from . import tracing
from .agent import respond, triage
from .lab import all_incidents
from .llm import make_backend
from .rag import RunbookIndex
from .state import LabState


def _ask(question: str) -> bool:
    if not sys.stdin.isatty():
        print(f"{question}\n  (no terminal to ask on, so this counts as 'no'. Use --yes to approve.)")
        return False
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="triage", description="AI incident triage agent (lab)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list the lab incidents")
    inj = sub.add_parser("inject", help="start an incident and fire its alert")
    inj.add_argument("incident_id")
    sub.add_parser("alerts", help="show firing alerts")
    for name in ("triage", "demo"):
        t = sub.add_parser(name, help="triage a firing alert" if name == "triage" else "inject, triage and (with approval) fix one incident")
        t.add_argument("incident_id", nargs="?", default="E04" if name == "demo" else None)
        t.add_argument("--backend", default="rules", choices=["rules", "ollama", "command"])
        t.add_argument("--model")
        t.add_argument("--command")
        t.add_argument("--mode", default="filtered", choices=["filtered", "raw"])
        t.add_argument("--yes", action="store_true", help="answer yes to the approval question")
        t.add_argument("--trace", action="store_true", help="record a span per step (see `trace-report`)")
    sub.add_parser("audit", help="print the audit log")
    tr = sub.add_parser("trace-report", help="latency per step, model tokens and cost from recorded traces")
    tr.add_argument("--file", help="trace file (default .lab_state/traces.jsonl)")
    tr.add_argument("--prices", help='USD per million tokens, JSON or a file: {"model": {"input": 1.0, "output": 5.0}}')
    tr.add_argument("--json", action="store_true", help="print the report as JSON")
    tr.add_argument("--otlp-endpoint", help="also send the spans to an OTLP/HTTP endpoint")
    w = sub.add_parser("watch", help="print alerts firing on the grafana-observability-stack exporter")
    w.add_argument("--url", default="http://localhost:9105/metrics")
    args = p.parse_args(argv)

    if args.cmd == "trace-report":
        path = args.file or str(tracing.Tracer().path)
        spans = tracing.load(path)
        if not spans:
            print(f"no spans in {path}; run a triage with --trace first")
            return 1
        rep = tracing.report(spans, tracing.load_prices(args.prices))
        print(json.dumps(rep, indent=1) if args.json else tracing.format_report(rep))
        if args.otlp_endpoint:
            print(f"sent {len(spans)} span(s) to {args.otlp_endpoint}: HTTP {tracing.export_otlp(spans, args.otlp_endpoint)}")
        return 0

    if getattr(args, "trace", False):
        tracing.enable()

    if args.cmd == "watch":
        from .exporter_source import fetch_alerts
        alerts = fetch_alerts(args.url)
        for a in alerts:
            print(f"[alert] {a['name']} ({a['severity']}) on {a['service']}: {a['metric']} = {a['value']} (threshold {a['threshold']})")
        print(f"{len(alerts)} alert(s) firing")
        return 0

    state, incidents = LabState(), all_incidents()
    if args.cmd == "list":
        for i in incidents.values():
            print(f"{i.id}  {i.alert['name']:<18} {i.service:<20} {i.flags}")
        return 0
    if args.cmd == "inject":
        state.inject(incidents[args.incident_id])
        print(f"alert firing: {incidents[args.incident_id].alert['name']} on {incidents[args.incident_id].service}")
        return 0
    if args.cmd == "alerts":
        print(json.dumps(state.alerts(), indent=1))
        return 0
    if args.cmd == "audit":
        for line in state.audit_lines():
            print(json.dumps(line))
        return 0

    iid = args.incident_id
    if args.cmd == "demo" or not iid:
        iid = iid or (state.alerts()[-1]["incident_id"] if state.alerts() else None)
        if args.cmd == "demo":
            state.inject(incidents[iid])
            a = incidents[iid].alert
            print(f"[alert] {a['name']} on {a['service']}: {a['metric']} = {a['value']}")
    if not iid:
        print("no firing alert; inject one first")
        return 1
    inc = state.incident(iid)
    llm = make_backend(args.backend, args.model, args.command)
    out = respond(inc, llm, state, RunbookIndex(), (lambda q: True) if args.yes else _ask, mode=args.mode)
    print("\n" + out["note"])
    return 0
