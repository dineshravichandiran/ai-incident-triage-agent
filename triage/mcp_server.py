"""MCP server exposing the four tools over stdio.

    python -m triage.mcp_server                       # all tools
    TRIAGE_ALERT_TYPE="High Latency" python -m triage.mcp_server   # only the tools that fit that alert

run_runbook is dry-run by default. A real run needs an approval id that a human
issued (`python -m triage approve ...` or the approval prompt); the server checks
it itself, so a client that skips its own confirmation still cannot run a fix.
"""
from __future__ import annotations

import os
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .state import LabState
from .tools import Tools, tools_for


def build_server(state: Optional[LabState] = None, alert_type: Optional[str] = None) -> FastMCP:
    tools = Tools(state)
    allowed = tools_for(alert_type, tools.index)
    server = FastMCP("incident-triage")

    if "get_alerts" in allowed:
        @server.tool()
        def get_alerts() -> list:
            """List the alerts that are firing right now."""
            return tools.get_alerts()

    if "get_logs" in allowed:
        @server.tool()
        def get_logs(service: str, minutes: int = 10, mode: str = "filtered") -> str:
            """Log summary for a service with a firing alert. mode 'filtered' (default) is WARN and above, repeats
            collapsed; mode 'raw' is every line from the last hour."""
            return tools.get_logs(service, minutes, mode)

    if "run_runbook" in allowed:
        @server.tool()
        def run_runbook(runbook_id: str, service: str, dry_run: bool = True, approval_id: Optional[str] = None) -> dict:
            """Check a runbook's condition and, only with dry_run=false and a human-issued approval_id, run its fix and
            verify it. Dry-run by default."""
            return tools.run_runbook(runbook_id, service, dry_run, approval_id)

    if "write_incident_note" in allowed:
        @server.tool()
        def write_incident_note(incident_id: str, text: str) -> dict:
            """Save an incident note (Sev1 to Sev4 format) for a person to review."""
            return tools.write_incident_note(incident_id, text)

    return server


if __name__ == "__main__":
    build_server(alert_type=os.environ.get("TRIAGE_ALERT_TYPE")).run()
