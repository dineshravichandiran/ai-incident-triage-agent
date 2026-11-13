import datetime as dt

from triage.context import KEEP_LEVELS, estimate_tokens, filter_logs, filtered_context, normalise, raw_context
from triage.lab import dev_set, eval_set


def test_normalise_collapses_numbers_and_ids():
    a = normalise("timeout acquiring connection from pool (size=20 in_use=20 waiting=16)")
    b = normalise("timeout acquiring connection from pool (size=20 in_use=20 waiting=90)")
    assert a == b


def test_filter_keeps_only_the_window_the_service_and_warn_or_above():
    for inc in dev_set():
        fired = dt.datetime.strptime(inc.alert["fired_at"], "%Y-%m-%dT%H:%M:%SZ")
        for p in filter_logs(inc):
            assert p.level in KEEP_LEVELS
            assert fired - dt.timedelta(minutes=10) <= dt.datetime.strptime(p.first_seen, "%Y-%m-%dT%H:%M:%SZ") <= fired


def test_repeated_lines_become_one_pattern_with_a_count():
    inc = next(i for i in dev_set() if i.truth_cause == "db-pool-exhausted")
    top = filter_logs(inc)[0]
    assert top.count > 5


def test_old_burst_is_outside_the_window():
    inc = next(i for i in dev_set() + eval_set() if "old-burst" in i.flags)
    assert "incident closed by on-call" not in filtered_context(inc)
    assert "incident closed by on-call" in raw_context(inc)


def test_filtered_context_is_much_smaller_than_raw():
    for inc in dev_set():
        assert estimate_tokens(filtered_context(inc)) * 10 < estimate_tokens(raw_context(inc))


def test_every_true_signal_line_survives_the_filter():
    """The filter may drop noise but never the evidence: each true signal pattern must still be in the filtered context."""
    from triage.context import filter_logs, normalise
    for inc in dev_set() + eval_set():
        kept = {p.pattern for p in filter_logs(inc)}
        for msg in set(inc.truth_signal):
            assert normalise(msg) in kept, (inc.id, msg)
