"""Tracing for the triage pipeline: one trace per incident, one span per step.

What it records, per span: name, start time, duration, status and attributes. Model
calls carry the OpenTelemetry GenAI attribute names (`gen_ai.request.model`,
`gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`), so the same spans can be sent to
any OTLP backend.

Off by default: with no `TRIAGE_TRACE` set and no tracer installed, `span()` does nothing and
writes no file. Turn it on with `--trace` on the CLI or `TRIAGE_TRACE=1`.

  - spans are appended to a JSON-lines file (default `.lab_state/traces.jsonl`)
  - `report()` summarises that file: latency per step, tokens, and cost
  - `export_otlp()` posts the spans to an OTLP/HTTP endpoint as JSON (standard library only)

Cost is only computed when you supply prices (`TRIAGE_PRICES` or `--prices`, USD per million
tokens). No prices are built in, because a hard-coded table goes stale and would put a made-up
number in the report.
"""
from __future__ import annotations

import contextlib
import contextvars
import datetime as dt
import json
import math
import os
import secrets
import time
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterator, List, Optional

_current: contextvars.ContextVar = contextvars.ContextVar("triage_span", default=None)
_tracer: Optional["Tracer"] = None


class Span:
    def __init__(self, tracer: "Tracer", name: str, trace_id: str, parent_id: Optional[str], attrs: dict) -> None:
        self.tracer, self.name, self.trace_id, self.parent_id = tracer, name, trace_id, parent_id
        self.span_id = secrets.token_hex(8)
        self.attrs: Dict[str, object] = dict(attrs)
        self.status = "ok"
        self.start_ns = time.time_ns()
        self._t0 = time.perf_counter()

    def set(self, **attrs) -> None:
        self.attrs.update({k: v for k, v in attrs.items() if v is not None})

    def to_json(self, duration_ms: float) -> dict:
        return {"trace_id": self.trace_id, "span_id": self.span_id, "parent_id": self.parent_id, "name": self.name,
                "start": dt.datetime.fromtimestamp(self.start_ns / 1e9, dt.timezone.utc).isoformat(timespec="milliseconds"),
                "start_ns": self.start_ns, "duration_ms": round(duration_ms, 3), "status": self.status, "attributes": self.attrs}


class Tracer:
    def __init__(self, path: Optional[os.PathLike] = None) -> None:
        self.path = Path(path or os.environ.get("TRIAGE_TRACE_FILE") or Path(os.environ.get("TRIAGE_STATE_DIR") or ".lab_state") / "traces.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def span(self, name: str, **attrs) -> Iterator[Span]:
        parent: Optional[Span] = _current.get()
        sp = Span(self, name, parent.trace_id if parent else secrets.token_hex(16), parent.span_id if parent else None, attrs)
        token = _current.set(sp)
        try:
            yield sp
        except BaseException as exc:
            sp.status = "error"
            sp.attrs["error.type"] = type(exc).__name__
            sp.attrs["error.message"] = str(exc)[:200]
            raise
        finally:
            _current.reset(token)
            line = json.dumps(sp.to_json((time.perf_counter() - sp._t0) * 1000))
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")


class _NoSpan:
    def set(self, **attrs) -> None:
        pass


def enable(path: Optional[os.PathLike] = None) -> Tracer:
    global _tracer
    _tracer = Tracer(path)
    return _tracer


def disable() -> None:
    global _tracer
    _tracer = None


def active() -> Optional[Tracer]:
    if _tracer is None and os.environ.get("TRIAGE_TRACE", "").lower() in ("1", "true", "yes"):
        enable()
    return _tracer


@contextlib.contextmanager
def span(name: str, **attrs) -> Iterator[object]:
    """A span when tracing is on, otherwise a do-nothing stand-in with the same `.set()`."""
    t = active()
    if t is None:
        yield _NoSpan()
        return
    with t.span(name, **attrs) as sp:
        yield sp


# ---- reading traces back ---------------------------------------------------------------
def load(path: os.PathLike) -> List[dict]:
    p = Path(path)
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def load_prices(spec: Optional[str] = None) -> Dict[str, Dict[str, float]]:
    """`{"model": {"input": usd_per_million, "output": usd_per_million}}`, inline JSON or a file path."""
    spec = spec or os.environ.get("TRIAGE_PRICES")
    if not spec:
        return {}
    text = Path(spec).read_text() if os.path.exists(spec) else spec
    return json.loads(text)


def _pct(values: List[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))] if s else 0.0


def report(spans: List[dict], prices: Optional[Dict[str, Dict[str, float]]] = None) -> dict:
    """Latency per step, model token use, cost (only with prices), and per-trace totals."""
    prices = prices or {}
    by_name: Dict[str, List[float]] = defaultdict(list)
    errors: Dict[str, int] = defaultdict(int)
    models: Dict[str, dict] = {}
    traces: Dict[str, dict] = {}
    unpriced = set()
    for s in spans:
        by_name[s["name"]].append(s["duration_ms"])
        if s["status"] != "ok":
            errors[s["name"]] += 1
        a = s["attributes"]
        t = traces.setdefault(s["trace_id"], {"incident": None, "ms": 0.0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "outcome": None})
        if s["parent_id"] is None:
            t["ms"] = s["duration_ms"]
            t["incident"] = a.get("incident.id")
            t["outcome"] = a.get("triage.status")
        if s["name"] == "llm.call":
            model = str(a.get("gen_ai.request.model", "?"))
            ti, to = int(a.get("gen_ai.usage.input_tokens", 0) or 0), int(a.get("gen_ai.usage.output_tokens", 0) or 0)
            m = models.setdefault(model, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": None, "ms": []})
            m["calls"] += 1; m["input_tokens"] += ti; m["output_tokens"] += to; m["ms"].append(s["duration_ms"])
            t["input_tokens"] += ti; t["output_tokens"] += to
            price = prices.get(model)
            if price:
                c = ti / 1e6 * price["input"] + to / 1e6 * price["output"]
                m["cost_usd"] = (m["cost_usd"] or 0.0) + c
                t["cost_usd"] += c
            else:
                unpriced.add(model)
    steps = {n: {"count": len(v), "mean_ms": round(sum(v) / len(v), 2), "p95_ms": round(_pct(v, 0.95), 2), "max_ms": round(max(v), 2),
                 "errors": errors.get(n, 0)} for n, v in by_name.items()}
    for m in models.values():
        ms = m.pop("ms")
        m["mean_ms"], m["p95_ms"] = round(sum(ms) / len(ms), 2), round(_pct(ms, 0.95), 2)
    return {"traces": len(traces), "steps": steps, "models": models, "per_trace": traces, "unpriced_models": sorted(unpriced)}


def format_report(rep: dict) -> str:
    out = [f"{rep['traces']} trace(s)", "", f"{'step':<22}{'count':>6}{'mean ms':>10}{'p95 ms':>10}{'max ms':>10}{'errors':>8}"]
    for name, s in sorted(rep["steps"].items(), key=lambda kv: -kv[1]["mean_ms"]):
        out.append(f"{name:<22}{s['count']:>6}{s['mean_ms']:>10}{s['p95_ms']:>10}{s['max_ms']:>10}{s['errors']:>8}")
    if rep["models"]:
        out += ["", f"{'model':<28}{'calls':>6}{'in tok':>10}{'out tok':>9}{'mean ms':>10}{'cost USD':>11}"]
        for name, m in rep["models"].items():
            cost = f"{m['cost_usd']:.4f}" if m["cost_usd"] is not None else "n/a"
            out.append(f"{name:<28}{m['calls']:>6}{m['input_tokens']:>10}{m['output_tokens']:>9}{m['mean_ms']:>10}{cost:>11}")
        if rep["unpriced_models"]:
            out.append("cost is n/a for models with no price set (TRIAGE_PRICES or --prices)")
    return "\n".join(out)


# ---- OTLP/HTTP (JSON) export -------------------------------------------------------------
def _any_value(v) -> dict:
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    return {"stringValue": str(v)}


def to_otlp(spans: List[dict], service_name: str = "ai-incident-triage-agent") -> dict:
    otlp_spans = []
    for s in spans:
        end_ns = s["start_ns"] + int(s["duration_ms"] * 1e6)
        otlp_spans.append({
            "traceId": s["trace_id"], "spanId": s["span_id"], **({"parentSpanId": s["parent_id"]} if s["parent_id"] else {}),
            "name": s["name"], "kind": 1, "startTimeUnixNano": str(s["start_ns"]), "endTimeUnixNano": str(end_ns),
            "attributes": [{"key": k, "value": _any_value(v)} for k, v in s["attributes"].items()],
            "status": {"code": 1 if s["status"] == "ok" else 2},
        })
    return {"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": service_name}}]},
                               "scopeSpans": [{"scope": {"name": "triage.tracing"}, "spans": otlp_spans}]}]}


def export_otlp(spans: List[dict], endpoint: Optional[str] = None, headers: Optional[Dict[str, str]] = None) -> int:
    """POST the spans to `<endpoint>/v1/traces`. Returns the HTTP status. Endpoint and headers can come from the
    standard OTEL_EXPORTER_OTLP_ENDPOINT and OTEL_EXPORTER_OTLP_HEADERS variables ("k=v,k2=v2")."""
    endpoint = (endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "").rstrip("/")
    if not endpoint:
        raise SystemExit("set OTEL_EXPORTER_OTLP_ENDPOINT or pass --otlp-endpoint")
    hdrs = {"Content-Type": "application/json"}
    for pair in filter(None, (headers and [f"{k}={v}" for k, v in headers.items()]) or os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "").split(",")):
        k, _, v = pair.partition("=")
        hdrs[k.strip()] = v.strip()
    req = urllib.request.Request(endpoint + "/v1/traces", data=json.dumps(to_otlp(spans)).encode(), headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status
