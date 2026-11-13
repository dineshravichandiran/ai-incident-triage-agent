"""Draft the incident note. The model proposes; this code writes. No model call, so no invented facts."""
from __future__ import annotations

from typing import List, Optional

SEV_BY_ALERT = {"Service Down": "Sev1", "High Error Rate": "Sev2", "Disk Almost Full": "Sev2",
                "High Latency": "Sev3", "High Memory Usage": "Sev3"}


def severity(alert_name: str) -> str:
    return SEV_BY_ALERT.get(alert_name, "Sev3")


def draft_note(incident_id: str, alert: dict, cause_category: str, likely_cause: str, evidence: List[str], confidence: float,
               runbook_id: Optional[str], fix_text: Optional[str], status: str, actions: List[str]) -> str:
    ev = "\n".join(f"  - {e}" for e in evidence) or "  - (none quoted)"
    acts = "\n".join(f"  - {a}" for a in actions) or "  - none yet"
    return (
        f"INCIDENT NOTE {incident_id}\n"
        f"Severity: {severity(alert['name'])}\n"
        f"Service: {alert['service']}\n"
        f"Alert: {alert['name']} fired {alert['fired_at']} ({alert['metric']} = {alert['value']}, threshold {alert['threshold']})\n"
        f"Status: {status}\n\n"
        f"Likely cause ({cause_category}, confidence {confidence:.2f}): {likely_cause}\n"
        f"Evidence:\n{ev}\n\n"
        f"Runbook: {runbook_id or 'none matched, escalate to a human'}\n"
        f"Proposed fix: {fix_text or 'n/a'}\n\n"
        f"Actions taken:\n{acts}\n"
        "\nDrafted by the triage agent. A person reviews it before it is filed.\n"
    )
