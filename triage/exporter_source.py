"""Alert source that reads the Prometheus exporter from grafana-observability-stack.

That exporter serves service_request_rate, service_error_rate_percent, service_latency_ms and
service_memory_usage_percent for eight services, and injects an incident now and then. This reads its
/metrics page and applies the same thresholds as that repo's Grafana alert rules (error rate over 15,
latency over 600 ms, memory over 90 percent), so an incident injected there shows up here as an alert.

Only the alerts come from the exporter. It serves no logs, so triage on real exporter alerts still needs
logs from somewhere else; in this repo the logs come from the lab.
"""
from __future__ import annotations

import re
import urllib.request
from typing import Dict, List

THRESHOLDS = {
    "service_error_rate_percent": ("High Error Rate", "critical", 15.0),
    "service_latency_ms": ("High Latency", "warning", 600.0),
    "service_memory_usage_percent": ("High Memory Usage", "warning", 90.0),
}
_LINE = re.compile(r'^(?P<name>[a-z_]+)\{service="(?P<service>[^"]+)"\}\s+(?P<value>[-+0-9.eE]+)\s*$')


def parse_metrics(text: str) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            out.setdefault(m["service"], {})[m["name"]] = float(m["value"])
    return out


def alerts_from_metrics(text: str) -> List[dict]:
    alerts = []
    for service, metrics in parse_metrics(text).items():
        for metric, (name, severity, threshold) in THRESHOLDS.items():
            value = metrics.get(metric)
            if value is not None and value > threshold:
                alerts.append({"name": name, "severity": severity, "metric": metric, "value": value,
                               "threshold": threshold, "service": service})
    return alerts


def fetch_alerts(url: str = "http://localhost:9105/metrics") -> List[dict]:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return alerts_from_metrics(resp.read().decode())
