import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from triage.cli import main
from triage.exporter_source import alerts_from_metrics, parse_metrics

PAGE = """# HELP service_error_rate_percent Error rate percentage
# TYPE service_error_rate_percent gauge
service_error_rate_percent{service="Billing API"} 22.4
service_error_rate_percent{service="Auth Service"} 0.6
service_latency_ms{service="Billing API"} 120.0
service_latency_ms{service="Auth Service"} 812.5
service_memory_usage_percent{service="Search Index"} 95.1
service_memory_usage_percent{service="CDN Edge"} 41.0
service_request_rate{service="CDN Edge"} 90.0
"""


def test_parse_metrics():
    m = parse_metrics(PAGE)
    assert m["Billing API"]["service_error_rate_percent"] == 22.4
    assert m["CDN Edge"]["service_request_rate"] == 90.0


def test_only_values_over_the_grafana_thresholds_become_alerts():
    got = {(a["name"], a["service"]) for a in alerts_from_metrics(PAGE)}
    assert got == {("High Error Rate", "Billing API"), ("High Latency", "Auth Service"), ("High Memory Usage", "Search Index")}


def test_watch_command_reads_a_live_page(capsys):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = PAGE.encode()
            self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert main(["watch", "--url", f"http://127.0.0.1:{srv.server_port}/metrics"]) == 0
    finally:
        srv.shutdown()
    out = capsys.readouterr().out
    assert "High Error Rate (critical) on Billing API" in out and "3 alert(s) firing" in out
