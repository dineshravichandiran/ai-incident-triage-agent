"""Context builder: turn a pile of logs into the small summary the model sees.

Filtered context keeps only what an on-call engineer would look at first:
the alerting service, the last 10 minutes before the alert, WARN and above, with
repeated lines collapsed into one pattern and a count. Raw context is the
baseline: every line the service logged in the last hour, unfiltered.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import List

from .lab import Incident, LogLine

WINDOW_MINUTES = 10
KEEP_LEVELS = ("WARN", "ERROR", "FATAL")

_TS = "%Y-%m-%dT%H:%M:%SZ"
_NORMALISERS = [
    (re.compile(r"\b[0-9a-f]{6,}\b"), "<id>"),
    (re.compile(r"\b\d+(?:\.\d+)?(?:ms|s|MB|%)"), "<n>"),
    (re.compile(r"(?<![\w.])v?\d+(?:\.\d+)+"), "<ver>"),
    (re.compile(r"\b\d+\b"), "<n>"),
]


def normalise(msg: str) -> str:
    """Collapse numbers, ids and versions so repeated lines become one pattern."""
    for rx, rep in _NORMALISERS:
        msg = rx.sub(rep, msg)
    return msg


def estimate_tokens(text: str) -> int:
    """Rough token count (about 4 characters per token). Used only to compare sizes."""
    return (len(text) + 3) // 4


@dataclass
class Pattern:
    level: str
    pattern: str
    count: int
    first_seen: str
    last_seen: str
    example: str


def filter_logs(incident: Incident, window_minutes: int = WINDOW_MINUTES) -> List[Pattern]:
    fired = dt.datetime.strptime(incident.alert["fired_at"], _TS)
    start = fired - dt.timedelta(minutes=window_minutes)
    grouped: "OrderedDict[tuple, Pattern]" = OrderedDict()
    for line in incident.logs:
        if line.service != incident.service or line.level not in KEEP_LEVELS:
            continue
        t = dt.datetime.strptime(line.ts, _TS)
        if not (start <= t <= fired):
            continue
        key = (line.level, normalise(line.msg))
        if key in grouped:
            p = grouped[key]
            p.count += 1
            p.last_seen = line.ts
        else:
            grouped[key] = Pattern(line.level, key[1], 1, line.ts, line.ts, line.msg)
    return sorted(grouped.values(), key=lambda p: (-p.count, p.level))


def metrics_block(incident: Incident) -> str:
    keys = ["service_request_rate", "service_error_rate_percent", "service_latency_ms",
            "service_memory_usage_percent", "service_disk_usage_percent", "up"]
    return "\n".join(f"{k} = {incident.metrics[k]}" for k in keys)


def alert_block(incident: Incident) -> str:
    a = incident.alert
    return (f"ALERT: {a['name']} (severity {a['severity']}) on {incident.service}\n"
            f"fired at {a['fired_at']}: {a['metric']} = {a['value']} (threshold {a['threshold']})")


def filtered_context(incident: Incident) -> str:
    pats = filter_logs(incident)
    rows = [f"{p.count:>3}x {p.level:<5} first {p.first_seen[11:19]} last {p.last_seen[11:19]}  {p.example}"
            if p.count > 1 else f"  1x {p.level:<5} at {p.first_seen[11:19]}  {p.example}" for p in pats]
    body = "\n".join(rows) if rows else "(no WARN or ERROR lines in the window)"
    return (f"{alert_block(incident)}\n\nMETRICS AT ALERT TIME\n{metrics_block(incident)}\n\n"
            f"LOG PATTERNS (service {incident.service}, last {WINDOW_MINUTES} minutes, WARN and above, repeats collapsed)\n{body}")


def raw_context(incident: Incident) -> str:
    own: List[LogLine] = [l for l in incident.logs if l.service == incident.service]
    body = "\n".join(l.render() for l in own)
    return (f"{alert_block(incident)}\n\nMETRICS AT ALERT TIME\n{metrics_block(incident)}\n\n"
            f"RAW LOGS (service {incident.service}, last 60 minutes, every line)\n{body}")


def build_context(incident: Incident, mode: str) -> str:
    if mode == "filtered":
        return filtered_context(incident)
    if mode == "raw":
        return raw_context(incident)
    raise ValueError(f"unknown mode {mode!r}")
