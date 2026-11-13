from triage.executor import run_runbook
from triage.lab import all_incidents


def _inject(state, iid="E04"):
    inc = all_incidents()[iid]
    state.inject(inc)
    return inc


def test_dry_run_never_changes_anything(state, index):
    inc = _inject(state)
    r = run_runbook(state, index, inc.truth_runbook, inc.service, dry_run=True)
    assert r["status"] == "problem_detected" and r["action_taken"] is False
    assert state.fault(inc.service) == inc.truth_cause


def test_real_run_without_approval_is_refused(state, index):
    inc = _inject(state)
    r = run_runbook(state, index, inc.truth_runbook, inc.service, dry_run=False)
    assert r["status"] == "approval_required"
    assert state.fault(inc.service) == inc.truth_cause


def test_a_made_up_approval_is_refused(state, index):
    inc = _inject(state)
    r = run_runbook(state, index, inc.truth_runbook, inc.service, dry_run=False, approval_id="apr-deadbeef")
    assert r["status"] == "approval_required"


def test_approval_for_another_runbook_or_service_is_refused(state, index):
    inc = _inject(state)
    apr = state.issue_approval(inc.id, "disk-full", inc.service, "human")
    assert run_runbook(state, index, inc.truth_runbook, inc.service, dry_run=False, approval_id=apr)["status"] == "approval_required"
    apr = state.issue_approval(inc.id, inc.truth_runbook, "some-other-service", "human")
    assert run_runbook(state, index, inc.truth_runbook, inc.service, dry_run=False, approval_id=apr)["status"] == "approval_required"


def test_approved_run_fixes_verifies_and_resolves_the_alert(state, index):
    inc = _inject(state)
    apr = state.issue_approval(inc.id, inc.truth_runbook, inc.service, "human")
    r = run_runbook(state, index, inc.truth_runbook, inc.service, dry_run=False, approval_id=apr)
    assert r["status"] == "healed"
    assert state.fault(inc.service) is None
    assert state.alerts("firing") == []


def test_an_approval_works_only_once(state, index):
    inc = _inject(state)
    apr = state.issue_approval(inc.id, inc.truth_runbook, inc.service, "human")
    run_runbook(state, index, inc.truth_runbook, inc.service, dry_run=False, approval_id=apr)
    state.inject(inc)   # the same fault comes back
    again = run_runbook(state, index, inc.truth_runbook, inc.service, dry_run=False, approval_id=apr)
    assert again["status"] == "approval_required"


def test_the_wrong_runbook_finds_no_problem_and_does_nothing(state, index):
    inc = _inject(state)
    apr = state.issue_approval(inc.id, "disk-full", inc.service, "human")
    r = run_runbook(state, index, "disk-full", inc.service, dry_run=False, approval_id=apr)
    assert r["status"] == "healthy" and r["action_taken"] is False
    assert state.fault(inc.service) == inc.truth_cause


def test_unknown_service_and_unknown_runbook(state, index):
    assert run_runbook(state, index, "disk-full", "ghost", dry_run=True)["status"] == "unknown"
    assert run_runbook(state, index, "no-such-runbook", "ghost")["status"] == "error"


def test_every_step_is_in_the_audit_log(state, index):
    inc = _inject(state)
    apr = state.issue_approval(inc.id, inc.truth_runbook, inc.service, "human")
    run_runbook(state, index, inc.truth_runbook, inc.service, dry_run=False, approval_id=apr)
    events = [l["event"] for l in state.audit_lines()]
    for e in ["incident_injected", "approval_issued", "runbook_start", "runbook_check", "runbook_fix", "runbook_verify", "runbook_result"]:
        assert e in events
