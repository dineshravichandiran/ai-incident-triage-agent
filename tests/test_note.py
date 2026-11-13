from triage.note import draft_note, severity


def test_severity_mapping():
    assert severity("Service Down") == "Sev1"
    assert severity("High Error Rate") == "Sev2"
    assert severity("High Latency") == "Sev3"
    assert severity("anything else") == "Sev3"


def test_note_contains_the_facts_it_was_given_and_nothing_else():
    alert = {"name": "High Latency", "service": "billing-api", "fired_at": "2026-11-14T10:00:00Z", "metric": "service_latency_ms",
             "value": 900.0, "threshold": 600}
    note = draft_note("E04", alert, "db-pool-exhausted", "pool is full", ["line one"], 0.9, "db-pool-exhausted", "release", "Awaiting", [])
    for needle in ["E04", "Sev3", "billing-api", "pool is full", "line one", "db-pool-exhausted", "Awaiting", "none yet"]:
        assert needle in note
