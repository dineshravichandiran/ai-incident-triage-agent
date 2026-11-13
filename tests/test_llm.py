import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from triage.llm import ANSWER_SCHEMA, CommandBackend, OllamaBackend, make_backend, validate_answer


def test_validate_answer_repairs_bad_output():
    out = validate_answer({"cause_category": "nonsense", "evidence": ["a", 3, "b", "c", "d"], "confidence": "high"})
    assert out["cause_category"] == "unknown" and out["confidence"] == 0.0
    assert out["evidence"] == ["a", "b", "c"]
    assert validate_answer({"cause_category": "disk-full", "confidence": 7})["confidence"] == 1.0


def test_command_backend_round_trip(tmp_path):
    script = tmp_path / "adapter.py"
    script.write_text(
        "import json,sys\nreq=json.load(sys.stdin)\n"
        "print(json.dumps({'data':{'cause_category':'disk-full','likely_cause':req['prompt'][:5],'evidence':[],'confidence':0.5},"
        "'usage':{'input_tokens':3,'output_tokens':4}}))\n")
    be = CommandBackend(f"{sys.executable} {script}")
    r = be.complete_json("sys", "hello world", ANSWER_SCHEMA)
    assert r.data["likely_cause"] == "hello" and r.usage["input_tokens"] == 3


def test_command_backend_reports_a_failing_adapter(tmp_path):
    script = tmp_path / "bad.py"
    script.write_text("import sys\nsys.stderr.write('boom')\nsys.exit(3)\n")
    with pytest.raises(RuntimeError, match="boom"):
        CommandBackend(f"{sys.executable} {script}").complete_json("s", "p")


def test_ollama_backend_talks_to_the_chat_api():
    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["path"] = self.path
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            reply = {"message": {"content": json.dumps({"cause_category": "dns-failure", "likely_cause": "x", "evidence": [], "confidence": 0.7})},
                     "prompt_eval_count": 11, "eval_count": 5}
            body = json.dumps(reply).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = OllamaBackend("some-model", f"http://127.0.0.1:{srv.server_port}").complete_json("sys", "prompt")
    finally:
        srv.shutdown()
    assert seen["path"] == "/api/chat" and seen["body"]["format"] == ANSWER_SCHEMA and seen["body"]["options"]["temperature"] == 0
    assert r.data["cause_category"] == "dns-failure" and r.usage == {"input_tokens": 11, "output_tokens": 5}


def test_make_backend_requires_what_it_needs(monkeypatch):
    monkeypatch.delenv("TRIAGE_LLM_CMD", raising=False)
    with pytest.raises(SystemExit):
        make_backend("command")
    with pytest.raises(SystemExit):
        make_backend("ollama")
    assert make_backend("rules").name == "rules-baseline"
