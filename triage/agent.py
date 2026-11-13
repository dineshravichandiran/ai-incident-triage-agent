"""The triage pipeline: alert -> context -> likely cause -> runbook step -> note -> approval -> fix -> verify.

The model is used for exactly one decision: reading the evidence and naming the
likely cause. Everything around it (filtering, runbook retrieval, the dry run,
the note, the approval gate, the verify step) is ordinary code, so the model
cannot skip a step or approve its own fix.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .context import build_context, estimate_tokens
from .lab import Incident
from .llm import ANSWER_SCHEMA, SYSTEM_PROMPT, validate_answer
from .note import draft_note
from .rag import RunbookIndex
from .state import LabState
from .tools import Tools


@dataclass
class TriageResult:
    incident_id: str
    mode: str
    backend: str
    cause_category: str = "unknown"
    likely_cause: str = ""
    evidence: List[str] = field(default_factory=list)
    confidence: float = 0.0
    grounded: List[bool] = field(default_factory=list)
    runbook_id: Optional[str] = None
    steps: List[dict] = field(default_factory=list)
    agree: bool = False
    context_tokens_est: int = 0
    usage: dict = field(default_factory=dict)
    latency_s: float = 0.0
    error: Optional[str] = None


def _norm(s: str) -> str:
    s = re.sub(r"^\s*\d{4}-\d\d-\d\dT[\d:]+Z\s+(INFO|WARN|ERROR|FATAL)\s+\S+\s+", "", s.strip())   # raw-line prefix
    s = re.sub(r"^\s*\d+x\s+(WARN|ERROR|FATAL)\s+(first \S+ last \S+\s+|at \S+\s+)?", "", s)        # pattern-row prefix
    s = re.sub(r"^(INFO|WARN|ERROR|FATAL)\s+", "", s)                                               # a bare level word
    return re.sub(r"\s+", " ", s).strip().strip("\"'`").lower()


def is_grounded(evidence_line: str, context: str) -> bool:
    """True when the line the model quoted really appears in the context it was given."""
    e = _norm(evidence_line)
    return bool(e) and e in re.sub(r"\s+", " ", context).lower()


def triage(incident: Incident, llm, index: RunbookIndex, mode: str = "filtered") -> TriageResult:
    context = build_context(incident, mode)
    res = TriageResult(incident.id, mode, getattr(llm, "name", "?"), context_tokens_est=estimate_tokens(context))
    try:
        out = llm.complete_json(SYSTEM_PROMPT, context, ANSWER_SCHEMA)
    except Exception as exc:  # a model failure must not crash triage; it becomes "unknown, escalate"
        res.error = f"{type(exc).__name__}: {exc}"[:300]
        return res
    ans = validate_answer(out.data)
    res.cause_category, res.likely_cause, res.evidence, res.confidence = (
        ans["cause_category"], ans["likely_cause"], ans["evidence"], ans["confidence"])
    res.grounded = [is_grounded(e, context) for e in res.evidence]
    res.usage, res.latency_s = out.usage, out.latency_s
    if res.cause_category != "unknown":
        query = res.likely_cause + " " + " ".join(res.evidence)
        hits = index.search(query, incident.alert["name"], incident.alert["severity"], k=3)
        if hits:
            res.runbook_id = hits[0].chunk.runbook_id
            res.steps = [{"runbook": h.chunk.runbook_id, "step": h.chunk.step_id, "kind": h.chunk.kind,
                          "score": round(h.score, 2), "text": h.chunk.text} for h in hits]
    # The category and the retrieved runbook come from two separate routes. A fix is only
    # proposed when they agree; if they differ, a person decides.
    res.agree = res.runbook_id == res.cause_category
    return res


def respond(incident: Incident, llm, state: LabState, index: RunbookIndex, approve: Callable[[str], bool],
            mode: str = "filtered", say: Callable[[str], None] = print) -> dict:
    """Full flow for one firing incident. `approve(question)` is the human in the loop."""
    tools = Tools(state, index)
    state.audit("agent", "triage_start", incident_id=incident.id, mode=mode)
    res = triage(incident, llm, index, mode)
    state.audit("agent", "triage_result", incident_id=incident.id, cause=res.cause_category, runbook=res.runbook_id,
                agree=res.agree, confidence=res.confidence, error=res.error)
    say(f"cause: {res.cause_category} (confidence {res.confidence:.2f})  {res.likely_cause}")
    for e in res.evidence:
        say(f"  evidence: {e}")
    actions: List[str] = []
    fix_text = next((s["text"] for s in res.steps if s["kind"] == "fix" and s["runbook"] == res.runbook_id), None)
    if fix_text is None and res.runbook_id:
        fix_text = next(s["fix"] for s in [{"fix": c.text} for c in index.chunks if c.runbook_id == res.runbook_id and c.kind == "fix"])
    status = "Investigating"
    outcome: dict = {"result": res, "status": None}

    if res.error or res.cause_category == "unknown" or not res.runbook_id:
        say("no confident cause or runbook: escalating to a human, no action taken")
        status = "Escalated: needs a human"
    elif not res.agree:
        say(f"the cause ({res.cause_category}) and the retrieved runbook ({res.runbook_id}) disagree: escalating, no fix proposed")
        status = "Escalated: cause and runbook disagree"
    else:
        dry = tools.run_runbook(res.runbook_id, incident.service, dry_run=True)
        say(f"dry-run: {dry['status']}" + (f", would run '{dry.get('would_run')}'" if dry.get("would_run") else ""))
        actions.append(f"dry-run of {res.runbook_id}: {dry['status']}")
        if dry["status"] != "problem_detected":
            status = "Escalated: runbook check did not confirm the problem"
        elif approve(f"Run the fix for {res.runbook_id} on {incident.service}? ({fix_text})"):
            approval_id = state.issue_approval(incident.id, res.runbook_id, incident.service, approved_by="human")
            real = tools.run_runbook(res.runbook_id, incident.service, dry_run=False, approval_id=approval_id)
            say(f"fix: {real['status']}" + (f" (verify: {real.get('verify')})" if real.get("verify") else ""))
            actions.append(f"fix {real.get('action')} approved by a human; result {real['status']}")
            status = "Mitigated, verified" if real["status"] == "healed" else "Fix failed: escalate"
            outcome["fix"] = real
        else:
            say("not approved: note only, no action")
            actions.append("fix proposed, not approved; no action taken")
            status = "Awaiting a human decision"

    note = draft_note(incident.id, incident.alert, res.cause_category, res.likely_cause, res.evidence, res.confidence,
                      res.runbook_id, fix_text, status, actions)
    tools.write_incident_note(incident.id, note)
    outcome["status"], outcome["note"] = status, note
    return outcome
