import json
from pathlib import Path

from triage.context import filtered_context, raw_context
from triage.lab import CAUSES, dev_set, eval_set, make_incident, manifest


def test_same_seed_gives_same_incident():
    a = make_incident("X", 7, "disk-full")
    b = make_incident("X", 7, "disk-full")
    assert a.digest() == b.digest()


def test_dev_and_eval_sets_cover_every_cause():
    assert {i.truth_cause for i in dev_set()} == set(CAUSES)
    assert {i.truth_cause for i in eval_set()} == set(CAUSES)
    assert len(dev_set()) == 10 and len(eval_set()) == 20


def test_dev_and_eval_do_not_share_seeds():
    assert not ({i.seed for i in dev_set()} & {i.seed for i in eval_set()})


def test_eval_set_is_frozen():
    """If the generator changes, the committed manifest no longer matches and this fails on purpose."""
    committed = json.loads((Path(__file__).parent.parent / "lab" / "manifest.json").read_text())
    assert committed == manifest(), "lab data changed: regenerate lab/manifest.json only on purpose and say so in the README"


def test_truth_is_never_in_the_text_the_model_sees():
    for inc in dev_set() + eval_set():
        for text in (filtered_context(inc), raw_context(inc)):
            assert inc.truth_cause not in text
            assert "truth" not in text.lower()


def test_to_json_can_hide_truth():
    d = dev_set()[0].to_json(with_truth=False)
    assert "truth_cause" not in d and "flags" not in d


def test_service_down_incidents_stop_logging_healthy_traffic():
    import datetime as dt
    inc = next(i for i in dev_set() + eval_set() if i.alert["name"] == "Service Down")
    fired = dt.datetime.strptime(inc.alert["fired_at"], "%Y-%m-%dT%H:%M:%SZ")
    last_info = max(dt.datetime.strptime(l.ts, "%Y-%m-%dT%H:%M:%SZ") for l in inc.logs if l.level == "INFO")
    assert fired - last_info >= dt.timedelta(minutes=6)


def test_every_difficulty_feature_appears_in_both_sets():
    for incidents in (dev_set(), eval_set()):
        flags = {f for i in incidents for f in i.flags}
        for needed in ("style:classic", "style:paraphrase", "style:mixed", "quiet", "consequence", "weak-decoy", "old-burst", "old-burst-large"):
            assert needed in flags, needed


def test_to_json_with_truth_keeps_the_signal_and_without_drops_it():
    inc = dev_set()[0]
    assert inc.to_json()["truth_signal"] and "truth_signal" not in inc.to_json(with_truth=False)


def test_quiet_incidents_have_only_a_handful_of_true_lines():
    for inc in dev_set() + eval_set():
        if "quiet" in inc.flags:
            assert len(inc.truth_signal) <= 10
