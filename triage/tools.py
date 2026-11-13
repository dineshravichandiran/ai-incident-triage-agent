"""The four tools the agent (and the MCP server) can call.

get_alerts, get_logs, run_runbook and write_incident_note. They are plain
methods here; mcp_server.py exposes the same methods over MCP.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import executor
from .context import build_context, filter_logs, alert_block, metrics_block, raw_context
from .rag import RunbookIndex
from .state import LabState

ALWAYS = ["get_alerts", "get_logs", "write_incident_note"]


def tools_for(alert_type: Optional[str], index: RunbookIndex) -> List[str]:
    """Only offer the tools that make sense for this alert. run_runbook is offered only when a runbook exists for it."""
    if alert_type is None:
        return ALWAYS + ["run_runbook"]
    has_runbook = any(alert_type in c.alert_types for c in index.chunks)
    return ALWAYS + (["run_runbook"] if has_runbook else [])


class Tools:
    def __init__(self, state: Optional[LabState] = None, index: Optional[RunbookIndex] = None) -> None:
        self.state = state or LabState()
        self.index = index or RunbookIndex()

    def get_alerts(self) -> List[Dict]:
        self.state.audit("agent", "get_alerts")
        return self.state.alerts("firing")

    def get_logs(self, service: str, minutes: int = 10, mode: str = "filtered") -> str:
        """Logs for the service that has a firing alert. `filtered` is the compact summary; `raw` is every line."""
        self.state.audit("agent", "get_logs", service=service, minutes=minutes, mode=mode)
        inc = self.state.firing_incident_for(service)
        if inc is None:
            return f"no firing alert for service {service}"
        if mode == "raw":
            return raw_context(inc)
        pats = filter_logs(inc, window_minutes=minutes)
        body = "\n".join(f"{p.count:>3}x {p.level:<5} {p.example}" for p in pats) or "(no WARN or ERROR lines)"
        return f"{alert_block(inc)}\n\nMETRICS\n{metrics_block(inc)}\n\nLOG PATTERNS (last {minutes} min)\n{body}"

    def run_runbook(self, runbook_id: str, service: str, dry_run: bool = True, approval_id: Optional[str] = None) -> Dict:
        return executor.run_runbook(self.state, self.index, runbook_id, service, dry_run=dry_run, approval_id=approval_id)

    def write_incident_note(self, incident_id: str, text: str) -> Dict:
        path = self.state.root / "notes" / f"{incident_id}.txt"
        path.write_text(text, encoding="utf-8")
        self.state.audit("agent", "note_written", incident_id=incident_id, path=str(path))
        return {"path": str(path), "bytes": len(text)}
