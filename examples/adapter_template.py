"""Template for the `command` backend: plug in any model without putting a key in this repo.

    TRIAGE_LLM_CMD="python examples/adapter_template.py" python -m triage demo E04 --backend command --yes

Contract:
  stdin  : {"system": "...", "prompt": "...", "schema": {JSON schema of the answer}}
  stdout : {"data": {cause_category, likely_cause, evidence, confidence},
            "usage": {"input_tokens": int, "output_tokens": int}}      # usage is optional but makes token numbers real
Exit non-zero (with a message on stderr) if the model call fails; the agent then escalates to a human instead of guessing.

This template calls no model. It answers "unknown", which the agent treats as "escalate".
"""
import json
import sys

request = json.load(sys.stdin)
# Replace this block with a call to your model. Ask it for JSON matching request["schema"], using request["system"] and
# request["prompt"], and return what it says in "data".
answer = {"cause_category": "unknown", "likely_cause": "template adapter: no model connected", "evidence": [], "confidence": 0.0}
print(json.dumps({"data": answer, "usage": {"input_tokens": 0, "output_tokens": 0}}))
