from triage.context import filter_logs
from triage.lab import dev_set


def test_metadata_filter_removes_runbooks_for_other_alerts(index):
    kept = {index.chunks[i].runbook_id for i in index.filter("Disk Almost Full", "critical")}
    assert kept == {"disk-full"}
    kept = {index.chunks[i].runbook_id for i in index.filter("Service Down", "critical")}
    assert kept == {"service-down", "config-error"}


def test_every_runbook_has_a_diagnose_fix_and_verify_step(index):
    for rb in index.runbooks.values():
        assert [s["kind"] for s in rb["steps"]] == ["diagnose", "fix", "verify"], rb["id"]


def test_right_runbook_comes_back_at_least_8_of_10_on_the_dev_set(index):
    ok = 0
    for inc in dev_set():
        query = " ".join(p.example for p in filter_logs(inc))
        ok += index.best_runbook(query, inc.alert["name"], inc.alert["severity"]) == inc.truth_runbook
    assert ok >= 8


def test_no_match_returns_none(index):
    assert index.best_runbook("zzzz qqqq", "High Latency", "warning") is None


def test_unknown_alert_type_has_no_candidates(index):
    assert index.search("anything", "Nonexistent Alert", "critical") == []
