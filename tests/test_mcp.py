"""Talk to the real MCP server over stdio, the way an MCP client would."""
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from triage.lab import all_incidents
from triage.state import LabState


def _session(state_dir, alert_type=None):
    env = {**os.environ, "TRIAGE_STATE_DIR": str(state_dir)}
    if alert_type:
        env["TRIAGE_ALERT_TYPE"] = alert_type
    return stdio_client(StdioServerParameters(command=sys.executable, args=["-m", "triage.mcp_server"], env=env))


def _payload(result):
    return result.structuredContent or json.loads(result.content[0].text)


def test_four_tools_are_listed(tmp_path):
    async def go():
        async with _session(tmp_path) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return (await s.list_tools()).tools
    tools = asyncio.run(go())
    assert sorted(t.name for t in tools) == ["get_alerts", "get_logs", "run_runbook", "write_incident_note"]
    for t in tools:   # every tool declares all four hints as real booleans
        a = t.annotations
        assert all(isinstance(getattr(a, h), bool) for h in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")), t.name
    by_name = {t.name: t.annotations for t in tools}
    assert by_name["get_logs"].readOnlyHint and not by_name["run_runbook"].readOnlyHint and by_name["run_runbook"].destructiveHint


def test_only_matching_tools_are_loaded_for_an_alert_type(tmp_path):
    async def go(alert):
        async with _session(tmp_path, alert) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return sorted(t.name for t in (await s.list_tools()).tools)
    assert "run_runbook" in asyncio.run(go("High Latency"))
    no_runbook = asyncio.run(go("Alert With No Runbook"))
    assert "run_runbook" not in no_runbook and "get_logs" in no_runbook


def test_run_runbook_over_mcp_is_dry_run_and_gated(tmp_path):
    state = LabState(tmp_path)
    inc = all_incidents()["E04"]
    state.inject(inc)

    async def go():
        async with _session(tmp_path) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                args = {"runbook_id": inc.truth_runbook, "service": inc.service}
                dry = _payload(await s.call_tool("run_runbook", args))
                refused = _payload(await s.call_tool("run_runbook", {**args, "dry_run": False}))
                faked = _payload(await s.call_tool("run_runbook", {**args, "dry_run": False, "approval_id": "apr-nope"}))
                apr = state.issue_approval(inc.id, inc.truth_runbook, inc.service, "human")   # what a person does
                real = _payload(await s.call_tool("run_runbook", {**args, "dry_run": False, "approval_id": apr}))
                return dry, refused, faked, real
    dry, refused, faked, real = asyncio.run(go())
    assert dry["status"] == "problem_detected" and dry["action_taken"] is False
    assert refused["status"] == faked["status"] == "approval_required"
    assert real["status"] == "healed"


def test_get_logs_and_note_over_mcp(tmp_path):
    state = LabState(tmp_path)
    inc = all_incidents()["E04"]
    state.inject(inc)

    async def go():
        async with _session(tmp_path) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                logs = (await s.call_tool("get_logs", {"service": inc.service})).content[0].text
                raw = (await s.call_tool("get_logs", {"service": inc.service, "mode": "raw"})).content[0].text
                note = _payload(await s.call_tool("write_incident_note", {"incident_id": inc.id, "text": "hello"}))
                return logs, raw, note
    logs, raw, note = asyncio.run(go())
    assert "LOG PATTERNS" in logs and len(raw) > 10 * len(logs)
    assert (tmp_path / "notes" / "E04.txt").read_text() == "hello"
