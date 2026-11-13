"""Run a runbook: check, then (only with a human approval) fix, then verify.

Adapted from runbook-automation-engine. The same contract: a check reports
healthy, problem or unknown; a fix is followed by a second check to prove it
worked; every stage goes to the audit log. Two differences:

  * dry-run is the default, and a real run needs a single-use approval that a
    human issued. The agent cannot create one.
  * the system under test is the simulated lab state, not a real machine.
"""
from __future__ import annotations

from typing import Dict

from .rag import RunbookIndex
from .state import LabState

HEALTHY, PROBLEM, UNKNOWN = 0, 1, 2


def run_check(state: LabState, runbook_id: str, service: str) -> tuple:
    fault = state.fault(service)
    if fault == "__unknown_service__":
        return UNKNOWN, f"no data for service {service}"
    if fault == runbook_id:
        return PROBLEM, f"{runbook_id} condition present on {service}"
    return HEALTHY, f"{runbook_id} condition not present on {service}"


def run_runbook(state: LabState, index: RunbookIndex, runbook_id: str, service: str, dry_run: bool = True,
                approval_id: str = None, actor: str = "agent") -> Dict:
    rb = index.runbooks.get(runbook_id)
    if rb is None:
        return {"status": "error", "detail": f"unknown runbook {runbook_id!r}"}
    fix = next(s for s in rb["steps"] if s["kind"] == "fix")
    state.audit(actor, "runbook_start", runbook=runbook_id, service=service, dry_run=dry_run)

    code, out = run_check(state, runbook_id, service)
    state.audit(actor, "runbook_check", runbook=runbook_id, service=service, exit_code=code, output=out)
    if code == UNKNOWN:
        return {"status": "unknown", "detail": out, "action_taken": False}
    if code == HEALTHY:
        return {"status": "healthy", "detail": out, "action_taken": False}

    if dry_run:
        state.audit(actor, "runbook_dry_run", runbook=runbook_id, would_run=fix["action"])
        return {"status": "problem_detected", "would_run": fix["action"], "fix_step": fix["text"], "action_taken": False,
                "detail": "dry-run: the fix was not run"}

    if not state.consume_approval(approval_id, runbook_id, service):
        state.audit(actor, "runbook_refused", runbook=runbook_id, service=service, reason="no valid approval")
        return {"status": "approval_required", "action_taken": False,
                "detail": "a fix needs a single-use approval issued by a human; none valid was given"}

    state.audit(actor, "runbook_fix", runbook=runbook_id, service=service, action=fix["action"], approval_id=approval_id)
    state.clear_fault(service)
    vcode, vout = run_check(state, runbook_id, service)
    state.audit(actor, "runbook_verify", runbook=runbook_id, service=service, exit_code=vcode, output=vout)
    healed = vcode == HEALTHY
    if healed:
        state.resolve_alerts(service)
    state.audit(actor, "runbook_result", runbook=runbook_id, service=service, status="healed" if healed else "remediation_failed")
    return {"status": "healed" if healed else "remediation_failed", "action": fix["action"], "action_taken": True, "verify": vout}
