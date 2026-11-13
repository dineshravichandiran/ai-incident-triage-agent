"""Model backends. The agent only needs one thing from a model: JSON in the shape we ask for.

  rules    an offline keyword baseline. It is NOT a language model; it exists so the
           project runs with no key and gives the evaluation something to beat.
  ollama   a local model through Ollama's HTTP API (structured output).
  command  any program you point it at. It gets a JSON request on stdin and must
           print a JSON reply on stdout (see examples/adapter_template.py). This is
           how a hosted model is plugged in without putting a key in this repo.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .lab import CAUSES

CATEGORIES = list(CAUSES) + ["unknown"]

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "cause_category": {"type": "string", "enum": CATEGORIES},
        "likely_cause": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "confidence": {"type": "number"},
    },
    "required": ["cause_category", "likely_cause", "evidence", "confidence"],
}

SYSTEM_PROMPT = (
    "You are an incident triage assistant for an on-call engineer. You are given one alert, a metrics snapshot and log "
    "evidence from the alerting service. Decide the most likely root cause.\n"
    "Use only the evidence given. Do not invent log lines. If the evidence does not support any category, answer "
    "\"unknown\" with a low confidence.\n"
    "Reply with JSON only: cause_category (one of the categories below), likely_cause (one sentence), evidence (up to 3 "
    "log lines copied exactly from the evidence), confidence (0 to 1).\n\nCategories:\n"
    + "\n".join(f"- {k}: {v['summary']}" for k, v in CAUSES.items())
    + "\n- unknown: the evidence does not point to any category above."
)


@dataclass
class LLMResult:
    data: dict
    usage: Dict[str, int] = field(default_factory=dict)
    latency_s: float = 0.0


def validate_answer(data: dict) -> dict:
    """Make the answer safe to use whatever the model sent back."""
    cat = data.get("cause_category")
    ev = data.get("evidence") or []
    try:
        conf = max(0.0, min(1.0, float(data.get("confidence", 0))))
    except (TypeError, ValueError):
        conf = 0.0
    return {
        "cause_category": cat if cat in CATEGORIES else "unknown",
        "likely_cause": str(data.get("likely_cause", ""))[:400],
        "evidence": [str(e)[:300] for e in ev if isinstance(e, str)][:3],
        "confidence": conf,
    }


# ---- offline baseline -----------------------------------------------------------------
_RULES = {
    "memory-leak": r"gc overhead|heap usage (?:8|9)\d|outofmemory|no eviction|gc pause [3-9]\d\d",
    "disk-full": r"no space left|filesystem /var usage (?:9\d|100)|failed to rotate",
    "service-down": r"exited unexpectedly|connection refused|is not running",
    "db-pool-exhausted": r"connection pool|acquiring connection|database connection",
    "bad-deploy": r"unhandled exception|500 internal server error",
    "downstream-timeout": r"timed out after|circuit breaker open|retry budget",
    "cert-expired": r"x509|certificate",
    "dns-failure": r"no such host|name resolution|resolver timeout after [2-5]\d\d\d",
    "quota-exceeded": r"\b429\b|quota exceeded|retry-after",
    "config-error": r"config reload failed|configuration validation|invalid value for key|last good config",
}


class RulesBackend:
    name = "rules-baseline"

    def complete_json(self, system: str, prompt: str, schema: dict = ANSWER_SCHEMA) -> LLMResult:
        t0 = time.time()
        scores: Dict[str, float] = {k: 0.0 for k in _RULES}
        lines_by: Dict[str, List[str]] = {k: [] for k in _RULES}
        for line in prompt.splitlines():
            if not re.search(r"\b(WARN|ERROR|FATAL)\b", line):
                continue
            m = re.match(r"\s*(\d+)x ", line)
            weight = int(m.group(1)) if m else 1
            for cat, rx in _RULES.items():
                if re.search(rx, line, re.I):
                    scores[cat] += weight
                    lines_by[cat].append(re.sub(r"^\s*\d+x \S+\s+first \S+ last \S+\s+|^\s*1x \S+\s+at \S+\s+", "", line).strip())
        best = max(scores, key=scores.get)
        total = sum(scores.values())
        if scores[best] < 5:
            data = {"cause_category": "unknown", "likely_cause": "no pattern matched", "evidence": [], "confidence": 0.1}
        else:
            data = {"cause_category": best, "likely_cause": CAUSES[best]["summary"], "evidence": lines_by[best][:3],
                    "confidence": round(scores[best] / total, 2)}
        return LLMResult(data, {}, time.time() - t0)   # no model, so no token usage to report


# ---- local model ------------------------------------------------------------------------
class OllamaBackend:
    def __init__(self, model: str, host: Optional[str] = None) -> None:
        self.model = model
        self.host = (host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
        self.name = f"ollama:{model}"

    def complete_json(self, system: str, prompt: str, schema: dict = ANSWER_SCHEMA) -> LLMResult:
        body = json.dumps({"model": self.model, "stream": False, "format": schema, "options": {"temperature": 0},
                           "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}).encode()
        t0 = time.time()
        req = urllib.request.Request(self.host + "/api/chat", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            out = json.loads(resp.read())
        data = json.loads(out["message"]["content"])
        usage = {"input_tokens": out.get("prompt_eval_count", 0), "output_tokens": out.get("eval_count", 0)}
        return LLMResult(data, usage, time.time() - t0)


# ---- any program --------------------------------------------------------------------------
class CommandBackend:
    """Run `command`, send {"system","prompt","schema"} on stdin, read {"data":{...},"usage":{...}} from stdout."""

    def __init__(self, command: str, label: str = "command") -> None:
        self.argv = shlex.split(command)
        self.name = label

    def complete_json(self, system: str, prompt: str, schema: dict = ANSWER_SCHEMA) -> LLMResult:
        t0 = time.time()
        done = subprocess.run(self.argv, input=json.dumps({"system": system, "prompt": prompt, "schema": schema}),
                              capture_output=True, text=True, timeout=300)
        if done.returncode != 0:
            raise RuntimeError(f"adapter failed: {done.stderr.strip()[:300]}")
        out = json.loads(done.stdout)
        return LLMResult(out["data"], out.get("usage", {}), time.time() - t0)


def make_backend(name: str, model: Optional[str] = None, command: Optional[str] = None):
    if name == "rules":
        return RulesBackend()
    if name == "ollama":
        if not model:
            raise SystemExit("--model is required for the ollama backend")
        return OllamaBackend(model)
    if name == "command":
        command = command or os.environ.get("TRIAGE_LLM_CMD")
        if not command:
            raise SystemExit("set TRIAGE_LLM_CMD or pass --command for the command backend")
        return CommandBackend(command)
    raise SystemExit(f"unknown backend {name!r}")
