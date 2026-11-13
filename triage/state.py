"""Lab state: firing alerts, simulated faults, approvals and the audit log.

Everything lives in one directory (default `.lab_state/`, override with
TRIAGE_STATE_DIR) as plain JSON, so a human can read it and a separate process,
such as the MCP server, sees the same data as the command line.

The faults here are simulated. A "fix" changes a field in this directory; it
never touches a real machine.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import secrets
from pathlib import Path
from typing import Dict, List, Optional

from .lab import Incident


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class LabState:
    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root or os.environ.get("TRIAGE_STATE_DIR") or ".lab_state")
        (self.root / "incidents").mkdir(parents=True, exist_ok=True)
        (self.root / "notes").mkdir(exist_ok=True)

    # ---- small JSON helpers
    def _read(self, name: str, default):
        p = self.root / name
        return json.loads(p.read_text()) if p.exists() else default

    def _write(self, name: str, data) -> None:
        (self.root / name).write_text(json.dumps(data, indent=1))

    # ---- audit log
    def audit(self, actor: str, event: str, **fields) -> None:
        line = json.dumps({"ts": _now(), "actor": actor, "event": event, **fields})
        with open(self.root / "audit.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def audit_lines(self) -> List[dict]:
        p = self.root / "audit.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    # ---- incidents, alerts, faults
    def inject(self, incident: Incident) -> None:
        """Start an incident: store its data, set the simulated fault, fire the alert."""
        (self.root / "incidents" / f"{incident.id}.json").write_text(json.dumps(incident.to_json()))
        faults = self._read("faults.json", {})
        faults[incident.service] = incident.truth_cause
        self._write("faults.json", faults)
        alerts = [a for a in self._read("alerts.json", []) if a["incident_id"] != incident.id]
        alerts.append({"incident_id": incident.id, "status": "firing", **incident.alert})
        self._write("alerts.json", alerts)
        self.audit("lab", "incident_injected", incident_id=incident.id, service=incident.service, alert=incident.alert["name"])

    def alerts(self, status: Optional[str] = "firing") -> List[dict]:
        return [a for a in self._read("alerts.json", []) if status is None or a["status"] == status]

    def incident(self, incident_id: str) -> Incident:
        p = self.root / "incidents" / f"{incident_id}.json"
        if not p.exists():
            raise KeyError(f"no incident {incident_id!r}")
        return Incident.from_json(json.loads(p.read_text()))

    def firing_incident_for(self, service: str) -> Optional[Incident]:
        for a in reversed(self.alerts("firing")):
            if a["service"] == service:
                return self.incident(a["incident_id"])
        return None

    def fault(self, service: str) -> Optional[str]:
        return self._read("faults.json", {}).get(service, "__unknown_service__")

    def clear_fault(self, service: str) -> None:
        faults = self._read("faults.json", {})
        faults[service] = None
        self._write("faults.json", faults)

    def resolve_alerts(self, service: str) -> None:
        alerts = self._read("alerts.json", [])
        for a in alerts:
            if a["service"] == service and a["status"] == "firing":
                a["status"] = "resolved"
        self._write("alerts.json", alerts)

    # ---- approvals: issued only by a human, single use, tied to one runbook on one service
    def issue_approval(self, incident_id: str, runbook_id: str, service: str, approved_by: str) -> str:
        approvals: Dict[str, dict] = self._read("approvals.json", {})
        approval_id = "apr-" + secrets.token_hex(4)
        approvals[approval_id] = {"incident_id": incident_id, "runbook_id": runbook_id, "service": service,
                                  "approved_by": approved_by, "issued_at": _now(), "used": False}
        self._write("approvals.json", approvals)
        self.audit(approved_by, "approval_issued", approval_id=approval_id, runbook_id=runbook_id, service=service)
        return approval_id

    def consume_approval(self, approval_id: Optional[str], runbook_id: str, service: str) -> bool:
        approvals: Dict[str, dict] = self._read("approvals.json", {})
        a = approvals.get(approval_id or "")
        if not a or a["used"] or a["runbook_id"] != runbook_id or a["service"] != service:
            return False
        a["used"] = True
        self._write("approvals.json", approvals)
        return True
