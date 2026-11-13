"""Command line: inject an incident, triage it, approve a fix, run the demo."""
from __future__ import annotations

import argparse
import json
import sys

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
    sub.add_parser("audit", help="print the audit log")
    w = sub.add_parser("watch", help="print alerts firing on the grafana-observability-stack exporter")
    w.add_argument("--url", default="http://localhost:9105/metrics")
    args = p.parse_args(argv)

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
