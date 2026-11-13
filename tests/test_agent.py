import pytest

from triage.agent import is_grounded, respond, triage
from triage.context import filter_logs
from triage.lab import CAUSES, all_incidents, dev_set
from triage.llm import LLMResult, RulesBackend


class Fixed:
    """A stand-in model that returns whatever answer the test gives it."""
    name = "fixed"

    def __init__(self, data=None, exc=None):
        self.data, self.exc = data, exc

    def complete_json(self, system, prompt, schema=None):
        if self.exc:
            raise self.exc
        return LLMResult(self.data, {"input_tokens": 1, "output_tokens": 1}, 0.0)


def oracle(inc):
    """A model that reads the evidence correctly. Used so these tests check the pipeline, not model quality."""
    top = filter_logs(inc)[0]
    return Fixed({"cause_category": inc.truth_cause, "likely_cause": CAUSES[inc.truth_cause]["summary"], "evidence": [top.example],
                  "confidence": 0.9})


def _setup(state, iid="E04"):
    inc = all_incidents()[iid]
    state.inject(inc)
    return inc


def test_full_flow_with_approval_fixes_and_notes(state, index):
    inc = _setup(state)
    out = respond(inc, oracle(inc), state, index, approve=lambda q: True, say=lambda s: None)
    assert out["status"] == "Mitigated, verified"
    assert "human" in out["note"] and "healed" in out["note"]
    assert state.fault(inc.service) is None
    assert (state.root / "notes" / f"{inc.id}.txt").exists()


def test_without_approval_nothing_is_changed(state, index):
    inc = _setup(state)
    out = respond(inc, oracle(inc), state, index, approve=lambda q: False, say=lambda s: None)
    assert out["status"] == "Awaiting a human decision"
    assert state.fault(inc.service) == inc.truth_cause
    assert not any(l["event"] == "runbook_fix" for l in state.audit_lines())


def test_the_model_cannot_approve_its_own_fix(state, index):
    inc = _setup(state)
    asked = []
    respond(inc, oracle(inc), state, index, approve=lambda q: asked.append(q) or False, say=lambda s: None)
    assert len(asked) == 1
    assert not any(l["event"] == "approval_issued" for l in state.audit_lines())


def test_cause_and_runbook_disagreeing_escalates(state, index):
    inc = _setup(state)   # a db-pool incident
    liar = Fixed({"cause_category": "disk-full", "likely_cause": "connection pool exhausted, requests wait for a database connection",
                  "evidence": [], "confidence": 0.9})
    out = respond(inc, liar, state, index, approve=lambda q: True, say=lambda s: None)
    assert out["status"].startswith("Escalated")
    assert state.fault(inc.service) == inc.truth_cause


@pytest.mark.parametrize("answer", [
    {"cause_category": "unknown", "likely_cause": "", "evidence": [], "confidence": 0.1},
    {"cause_category": "not-a-category", "likely_cause": "x", "evidence": [], "confidence": 5},
])
def test_unknown_or_invalid_answers_escalate(state, index, answer):
    inc = _setup(state)
    out = respond(inc, Fixed(answer), state, index, approve=lambda q: True, say=lambda s: None)
    assert out["status"].startswith("Escalated")
    assert state.fault(inc.service) == inc.truth_cause


def test_a_model_failure_escalates_instead_of_crashing(state, index):
    inc = _setup(state)
    out = respond(inc, Fixed(exc=RuntimeError("adapter down")), state, index, approve=lambda q: True, say=lambda s: None)
    assert out["status"].startswith("Escalated") and out["result"].error


def test_wrong_cause_for_the_wrong_alert_type_finds_no_runbook(state, index):
    """E04 is a latency alert. Disk words cannot reach the disk runbook because it does not apply to latency alerts."""
    inc = _setup(state, "E04")
    wrong = Fixed({"cause_category": "disk-full", "likely_cause": "filesystem out of space, no space left on device, writes failing",
                   "evidence": [], "confidence": 0.9})
    res = triage(inc, wrong, index)
    assert res.runbook_id != "disk-full" and res.agree is False
    out = respond(inc, wrong, state, index, approve=lambda q: True, say=lambda s: None)
    assert out["status"].startswith("Escalated") and state.fault(inc.service) == inc.truth_cause


def test_wrong_cause_that_does_match_a_runbook_is_stopped_by_the_runbook_check(state, index):
    """E05 is a bad-deploy error-rate alert. A wrong 'disk-full' answer does reach the disk runbook (it applies to error-rate
    alerts) and the model and retrieval agree, so only the runbook's own check can stop it."""
    inc = _setup(state, "E05")
    assert inc.truth_cause == "bad-deploy"
    wrong = Fixed({"cause_category": "disk-full", "likely_cause": "filesystem out of space, no space left on device, writes failing",
                   "evidence": [], "confidence": 0.9})
    res = triage(inc, wrong, index)
    assert res.runbook_id == "disk-full" and res.agree is True
    out = respond(inc, wrong, state, index, approve=lambda q: True, say=lambda s: None)
    assert out["status"] == "Escalated: runbook check did not confirm the problem"
    assert state.fault(inc.service) == "bad-deploy"
    assert not any(l["event"] in ("approval_issued", "runbook_fix") for l in state.audit_lines())


def test_grounding_check_accepts_real_lines_and_rejects_invented_ones():
    ctx = ("  17x ERROR first 10:34:01 last 10:40:56  retry budget exhausted calling redis-cache\n"
           "2026-11-14T10:34:01Z ERROR billing-api upstream redis-cache request timed out after 10000ms")
    assert is_grounded("retry budget exhausted calling redis-cache", ctx)
    assert is_grounded("2026-11-14T10:34:01Z ERROR billing-api upstream redis-cache request timed out after 10000ms", ctx)
    assert is_grounded("17x ERROR first 10:34:01 last 10:40:56  retry budget exhausted calling redis-cache", ctx)
    assert is_grounded("ERROR  retry budget exhausted calling redis-cache", ctx)      # a leading level word is allowed
    assert not is_grounded("ERROR  retry budget exhausted calling payments-gateway", ctx)   # the message must still match
    assert not is_grounded("kernel panic on node 7", ctx)
    assert not is_grounded("", ctx)


def test_rules_baseline_reads_textbook_wording_but_not_every_paraphrase(index):
    """The offline baseline is a keyword list. It should get classic wording right and is allowed to miss paraphrases."""
    classic = [i for i in dev_set() if "style:classic" in i.flags]
    assert classic
    for inc in classic:
        res = triage(inc, RulesBackend(), index, "filtered")
        assert res.cause_category == inc.truth_cause and all(res.grounded), inc.id
    paraphrased = [i for i in dev_set() if "style:paraphrase" in i.flags]
    assert any(triage(i, RulesBackend(), index, "filtered").cause_category != i.truth_cause for i in paraphrased)
