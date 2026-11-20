import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from triage import tracing
from triage.agent import respond, triage
from triage.cli import main
from triage.lab import all_incidents
from triage.llm import LLMResult, RulesBackend


class Model:
    """A stand-in model that reports token use, like a hosted one."""
    name = "stub-model"

    def __init__(self, data, usage=None):
        self.data, self.usage = data, usage or {"input_tokens": 2000, "output_tokens": 100}

    def complete_json(self, system, prompt, schema=None):
        return LLMResult(self.data, dict(self.usage), 0.25)


@pytest.fixture(autouse=True)
def off():
    tracing.disable()
    yield
    tracing.disable()


@pytest.fixture
def tracer(tmp_path):
    return tracing.enable(tmp_path / "traces.jsonl")


def test_nested_spans_share_a_trace_and_record_parent(tracer):
    with tracing.span("outer", a=1) as o:
        with tracing.span("inner") as i:
            i.set(x="y")
        o.set(done=True)
    spans = tracing.load(tracer.path)
    inner, outer = spans  # a child is written when it ends, so it comes first
    assert inner["name"] == "inner" and outer["name"] == "outer"
    assert inner["trace_id"] == outer["trace_id"] and inner["parent_id"] == outer["span_id"] and outer["parent_id"] is None
    assert outer["attributes"] == {"a": 1, "done": True} and inner["attributes"] == {"x": "y"}
    assert outer["duration_ms"] >= inner["duration_ms"] >= 0


def test_an_exception_marks_the_span_as_error_and_still_propagates(tracer):
    with pytest.raises(ValueError):
        with tracing.span("boom"):
            raise ValueError("bad input")
    (s,) = tracing.load(tracer.path)
    assert s["status"] == "error" and s["attributes"]["error.type"] == "ValueError"


def test_tracing_off_writes_nothing_and_is_harmless(tmp_path, monkeypatch):
    monkeypatch.delenv("TRIAGE_TRACE", raising=False)
    monkeypatch.setenv("TRIAGE_STATE_DIR", str(tmp_path))
    with tracing.span("x") as sp:
        sp.set(a=1)
    assert not (tmp_path / "traces.jsonl").exists()


def test_env_variable_turns_tracing_on(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIAGE_TRACE", "1")
    monkeypatch.setenv("TRIAGE_TRACE_FILE", str(tmp_path / "t.jsonl"))
    with tracing.span("x"):
        pass
    assert len(tracing.load(tmp_path / "t.jsonl")) == 1


def test_a_full_triage_records_one_trace_with_every_step(tracer, state, index):
    inc = all_incidents()["E04"]
    state.inject(inc)
    respond(inc, RulesBackend(), state, index, approve=lambda q: True, say=lambda s: None)
    spans = tracing.load(tracer.path)
    assert len({s["trace_id"] for s in spans}) == 1
    names = [s["name"] for s in spans]
    for step in ("context.build", "llm.call", "runbook.retrieve", "runbook.dry_run", "approval.wait", "runbook.fix", "note.write", "triage", "triage.respond"):
        assert step in names, step
    root = next(s for s in spans if s["parent_id"] is None)
    assert root["name"] == "triage.respond" and root["attributes"]["incident.id"] == "E04"
    assert root["attributes"]["triage.status"] == "Mitigated, verified"
    assert next(s for s in spans if s["name"] == "approval.wait")["attributes"]["approval.granted"] is True


def test_llm_span_carries_genai_token_attributes(tracer, index):
    inc = all_incidents()["E04"]
    triage(inc, Model({"cause_category": "unknown", "likely_cause": "", "evidence": [], "confidence": 0.1}), index)
    call = next(s for s in tracing.load(tracer.path) if s["name"] == "llm.call")
    a = call["attributes"]
    assert a["gen_ai.request.model"] == "stub-model"
    assert a["gen_ai.usage.input_tokens"] == 2000 and a["gen_ai.usage.output_tokens"] == 100


def test_a_model_failure_is_an_error_span_and_triage_still_escalates(tracer, index):
    class Broken:
        name = "broken"

        def complete_json(self, *a, **k):
            raise RuntimeError("model down")

    res = triage(all_incidents()["E04"], Broken(), index)
    assert res.error and res.cause_category == "unknown"
    call = next(s for s in tracing.load(tracer.path) if s["name"] == "llm.call")
    assert call["status"] == "error"


def test_not_granting_approval_is_recorded(tracer, state, index):
    inc = all_incidents()["E04"]
    state.inject(inc)
    respond(inc, RulesBackend(), state, index, approve=lambda q: False, say=lambda s: None)
    names = [s["name"] for s in tracing.load(tracer.path)]
    assert "approval.wait" in names and "runbook.fix" not in names


def _two_traces(tracer, index):
    inc = all_incidents()["E04"]
    for _ in range(2):
        triage(inc, Model({"cause_category": "unknown", "likely_cause": "", "evidence": [], "confidence": 0.1}), index)
    return tracing.load(tracer.path)


def test_report_sums_tokens_and_counts_steps(tracer, index):
    rep = tracing.report(_two_traces(tracer, index))
    assert rep["traces"] == 2
    assert rep["steps"]["llm.call"]["count"] == 2
    m = rep["models"]["stub-model"]
    assert (m["calls"], m["input_tokens"], m["output_tokens"]) == (2, 4000, 200)
    assert m["cost_usd"] is None and rep["unpriced_models"] == ["stub-model"]


def test_report_cost_comes_only_from_supplied_prices(tracer, index):
    rep = tracing.report(_two_traces(tracer, index), {"stub-model": {"input": 3.0, "output": 15.0}})
    # 4000 input tokens at $3 per million + 200 output tokens at $15 per million
    assert rep["models"]["stub-model"]["cost_usd"] == pytest.approx(4000 / 1e6 * 3 + 200 / 1e6 * 15)
    assert rep["unpriced_models"] == []
    assert all(t["cost_usd"] == pytest.approx(2000 / 1e6 * 3 + 100 / 1e6 * 15) for t in rep["per_trace"].values())


def test_prices_load_from_inline_json_or_a_file(tmp_path, monkeypatch):
    monkeypatch.delenv("TRIAGE_PRICES", raising=False)
    spec = {"m": {"input": 1.0, "output": 2.0}}
    assert tracing.load_prices(json.dumps(spec)) == spec
    f = tmp_path / "p.json"
    f.write_text(json.dumps(spec))
    assert tracing.load_prices(str(f)) == spec
    assert tracing.load_prices(None) == {}


def test_percentile_is_nearest_rank():
    assert tracing._pct([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.95) == 10
    assert tracing._pct([5], 0.95) == 5 and tracing._pct([], 0.95) == 0.0


def test_cli_trace_flag_then_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TRIAGE_STATE_DIR", str(tmp_path))
    assert main(["demo", "E04", "--yes", "--trace"]) == 0
    assert (tmp_path / "traces.jsonl").exists()
    capsys.readouterr()
    assert main(["trace-report"]) == 0
    out = capsys.readouterr().out
    assert "1 trace(s)" in out and "llm.call" in out and "runbook.fix" in out


def test_cli_report_with_no_traces_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TRIAGE_STATE_DIR", str(tmp_path))
    assert main(["trace-report"]) == 1
    assert "no spans" in capsys.readouterr().out


def test_otlp_payload_shape_and_post(tracer, index):
    spans = _two_traces(tracer, index)
    got = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got["path"], got["auth"] = self.path, self.headers.get("Authorization")
            got["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    status = tracing.export_otlp(spans, f"http://127.0.0.1:{srv.server_port}", {"Authorization": "Basic abc"})
    srv.server_close()
    assert status == 200 and got["path"] == "/v1/traces" and got["auth"] == "Basic abc"
    sent = got["body"]["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert len(sent) == len(spans)
    llm = next(s for s in sent if s["name"] == "llm.call")
    attrs = {a["key"]: a["value"] for a in llm["attributes"]}
    assert attrs["gen_ai.usage.input_tokens"] == {"intValue": "2000"} and len(llm["traceId"]) == 32 and len(llm["spanId"]) == 16
    assert int(llm["endTimeUnixNano"]) >= int(llm["startTimeUnixNano"])
    child = next(s for s in sent if s["name"] == "context.build")
    assert child["parentSpanId"]


def test_threads_get_separate_traces(tracer):
    def work():
        with tracing.span("job"):
            with tracing.span("step"):
                pass

    ts = [threading.Thread(target=work) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    spans = tracing.load(tracer.path)
    assert len({s["trace_id"] for s in spans}) == 3 and len(spans) == 6
