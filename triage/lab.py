"""A synthetic incident lab with known root causes.

Every incident is generated from a seed, so the same seed always gives the same
alert, metrics and log stream. Each incident has a ground-truth cause, which is
what lets the evaluation say "right" or "wrong" without a human grading it.

Version history: the first version of this lab was too easy (a keyword baseline scored 20 of 20 on the eval set), so
before any language model was run on it the data was made harder: paraphrased wording, quiet failures with few lines,
generic errors that are a consequence of the real fault, and a large old incident in the raw hour.

The eight services and the metric names mirror the exporter in
grafana-observability-stack. The logs are synthetic: that exporter only
produces metrics, so the log lines here are written for this lab.

This measures the pipeline (filtering, retrieval, tool use, approval) on data
its author wrote. It does not measure how the agent would do on real logs.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import random
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

SERVICES = [
    "app-gateway", "auth-service", "billing-api", "order-processing",
    "search-index", "notification-service", "database-cluster", "cdn-edge",
]
DEPENDENCIES = ["payments-gateway", "inventory-svc", "ldap-sso", "smtp-relay", "object-store", "postgres-primary", "redis-cache"]
HANDLERS = ["CheckoutHandler", "SessionHandler", "InvoiceHandler", "SearchHandler", "NotifyHandler"]
PATHS = ["/api/orders", "/api/users", "/api/search", "/api/invoices", "/api/sessions", "/api/items"]

# Alert rules: the first three match grafana-observability-stack's rules.yml.
# The last two are lab-defined.
ALERTS = {
    "High Error Rate": {"severity": "critical", "metric": "service_error_rate_percent", "threshold": 15},
    "High Latency": {"severity": "warning", "metric": "service_latency_ms", "threshold": 600},
    "High Memory Usage": {"severity": "warning", "metric": "service_memory_usage_percent", "threshold": 90},
    "Disk Almost Full": {"severity": "critical", "metric": "service_disk_usage_percent", "threshold": 90},
    "Service Down": {"severity": "critical", "metric": "up", "threshold": 1},
}

# The failure taxonomy. `alerts` lists the alert types a cause can show up as.
CAUSES: Dict[str, dict] = {
    "memory-leak": {
        "summary": "Memory grows without being released (leak or unbounded cache); heavy garbage collection.",
        "alerts": ["High Memory Usage", "High Latency"],
    },
    "disk-full": {
        "summary": "A filesystem is out of space; writes and log rotation fail.",
        "alerts": ["Disk Almost Full", "High Error Rate"],
    },
    "service-down": {
        "summary": "The process crashed or was killed and is not running.",
        "alerts": ["Service Down"],
    },
    "db-pool-exhausted": {
        "summary": "The database connection pool is exhausted; requests wait for a connection.",
        "alerts": ["High Latency", "High Error Rate"],
    },
    "bad-deploy": {
        "summary": "A new release introduced an exception that fails requests.",
        "alerts": ["High Error Rate"],
    },
    "downstream-timeout": {
        "summary": "A dependency is slow or unreachable; calls to it time out.",
        "alerts": ["High Latency", "High Error Rate"],
    },
    "cert-expired": {
        "summary": "A TLS certificate has expired; handshakes with a dependency fail.",
        "alerts": ["High Error Rate"],
    },
    "dns-failure": {
        "summary": "Name resolution fails; the service cannot find its dependencies.",
        "alerts": ["High Error Rate", "High Latency"],
    },
    "quota-exceeded": {
        "summary": "An upstream provider is rejecting calls with rate-limit or quota errors.",
        "alerts": ["High Error Rate"],
    },
    "config-error": {
        "summary": "An invalid configuration was loaded, so the service refuses to start or reload.",
        "alerts": ["Service Down", "High Error Rate"],
    },
}


@dataclass
class LogLine:
    ts: str
    level: str
    service: str
    msg: str

    def render(self) -> str:
        return f"{self.ts} {self.level:<5} {self.service} {self.msg}"


@dataclass
class Incident:
    id: str
    seed: int
    service: str
    alert: dict                       # name, severity, metric, value, threshold, fired_at
    metrics: Dict[str, float]
    logs: List[LogLine] = field(default_factory=list)
    truth_cause: str = ""
    truth_runbook: str = ""
    flags: List[str] = field(default_factory=list)   # which difficulty features are present
    truth_signal: List[str] = field(default_factory=list)   # the true cause's log messages (truth only)

    def to_json(self, with_truth: bool = True) -> dict:
        d = asdict(self)
        if not with_truth:
            d.pop("truth_cause"), d.pop("truth_runbook"), d.pop("flags"), d.pop("truth_signal")
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Incident":
        d = dict(d)
        d["logs"] = [LogLine(**l) for l in d.get("logs", [])]
        return cls(**d)

    def digest(self) -> str:
        blob = json.dumps(self.to_json(), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:16]


def _ts(base: dt.datetime, seconds: float) -> str:
    return (base + dt.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- signal lines for each cause -------------------------------------------------
# Two wordings of every cause. "classic" is the textbook phrasing; "paraphrase" says the same thing the way a
# different framework or team would. An incident uses one wording, or a mix, so a keyword list written for one
# will miss the other.
def _classic(cause: str, r: random.Random, svc: str, dep: str, ver: str, step: float):
    n = lambda a, b: r.randint(a, b)
    return {
        "memory-leak": [
            ("WARN", f"heap usage {int(78 + 18 * step)}% of 2048MB, gc pause {n(300, 900)}ms"),
            ("ERROR", f"GC overhead limit exceeded: {n(70, 96)}% of time spent in GC"),
            ("WARN", f"cache sessions grew to {n(180, 400) * 1000} entries, no eviction since startup"),
            ("ERROR", f"OutOfMemoryError: Java heap space in worker-{n(1, 8)}")],
        "disk-full": [
            ("ERROR", f"write failed: No space left on device (path=/var/log/{svc}/app.log)"),
            ("ERROR", "failed to rotate log: no space left on device"),
            ("WARN", f"filesystem /var usage {n(95, 100)}%, {n(0, 40)}MB free"),
            ("ERROR", f"cannot create temp file /var/tmp/upload-{n(1000, 9999)}: No space left on device")],
        "service-down": [
            ("FATAL", f"process exited unexpectedly code={r.choice([137, 139])} signal=SIG{r.choice(['KILL', 'SEGV'])}"),
            ("ERROR", f"health check failed: connection refused on :{r.choice([8080, 8443, 9000])}"),
            ("ERROR", f"supervisor: service {svc} is not running, restart attempt {n(3, 12)} failed")],
        "db-pool-exhausted": [
            ("ERROR", f"timeout acquiring connection from pool (size=20 in_use=20 waiting={n(15, 90)})"),
            ("ERROR", f"could not obtain database connection within {n(5, 30) * 1000}ms"),
            ("WARN", f"connection pool exhausted: active=20/20 pending={n(10, 80)}")],
        "bad-deploy": [
            ("ERROR", f"unhandled exception in {r.choice(HANDLERS)} (build {ver}): NullPointerException"),
            ("ERROR", f"500 Internal Server Error {r.choice(['GET', 'POST'])} {r.choice(PATHS)} build={ver}"),
            ("ERROR", f"unhandled exception in {r.choice(HANDLERS)} (build {ver}): KeyError 'customer_tier'")],
        "downstream-timeout": [
            ("ERROR", f"upstream {dep} request timed out after {n(5, 30) * 1000}ms"),
            ("WARN", f"circuit breaker OPEN for {dep} (failures {n(20, 60)}/{n(20, 60)})"),
            ("ERROR", f"retry budget exhausted calling {dep}")],
        "cert-expired": [
            ("ERROR", f"TLS handshake failed with {dep}: x509: certificate has expired or is not yet valid"),
            ("ERROR", f"tls: failed to verify certificate: certificate expired {n(1, 40)} hours ago ({dep})")],
        "dns-failure": [
            ("ERROR", f"dial tcp: lookup {dep}.internal on 10.0.0.2:53: no such host"),
            ("ERROR", f"Temporary failure in name resolution for {dep}.internal"),
            ("WARN", f"resolver timeout after {n(2000, 5000)}ms for {dep}.internal")],
        "quota-exceeded": [
            ("ERROR", f"upstream {dep} returned 429 Too Many Requests: quota exceeded (limit {r.choice([600, 1000, 5000])}/min)"),
            ("WARN", f"Retry-After: {n(10, 60)}s from {dep}, backing off")],
        "config-error": [
            ("ERROR", f"config reload failed: invalid value for key '{r.choice(['pool.size', 'timeout.ms', 'listen.port'])}' at line {n(3, 80)}"),
            ("FATAL", f"refusing to start: configuration validation failed ({n(1, 5)} errors)"),
            ("WARN", "falling back to last good config v41")],
    }[cause]


def _paraphrase(cause: str, r: random.Random, svc: str, dep: str, ver: str, step: float):
    n = lambda a, b: r.randint(a, b)
    return {
        "memory-leak": [
            ("ERROR", f"worker-{n(1, 8)} killed by memory watchdog: resident set {r.uniform(3.6, 4.2):.1f}GB exceeds soft limit 3.5GB"),
            ("WARN", f"allocation rate {r.uniform(1.4, 2.4):.1f}GB/s, old generation {n(92, 99)}% full after full collection"),
            ("WARN", f"memory pressure: RSS grew {n(30, 70)}MB in the last 5 minutes and none was returned")],
        "disk-full": [
            ("ERROR", f"ENOSPC: cannot append to journal /var/lib/{svc}/wal"),
            ("ERROR", "upload spool is full: 0 bytes available on /data"),
            ("WARN", f"/var is {n(98, 100)}% full, {n(0, 12)} inodes free")],
        "service-down": [
            ("FATAL", f"panic: runtime error: invalid memory address or nil pointer dereference; goroutine {n(1, 40)} [running]"),
            ("ERROR", f"proxy: no healthy backend for {svc} (0/3 instances up)"),
            ("ERROR", f"systemd: {svc}.service: Main process exited, status={r.choice(['137/KILL', '139/SEGV'])}")],
        "db-pool-exhausted": [
            ("ERROR", f"HikariPool-1 - Connection is not available, request timed out after {n(10, 30) * 1000}ms"),
            ("WARN", f"all 20 database sessions are checked out; {n(10, 90)} callers queued"),
            ("ERROR", "FATAL: sorry, too many clients already")],
        "bad-deploy": [
            ("ERROR", f"Traceback (most recent call last): AttributeError: 'NoneType' object has no attribute 'tier' (release {ver})"),
            ("ERROR", f"panic recovered in {r.choice(HANDLERS)}: index out of range [3] with length 3 (release {ver})"),
            ("WARN", f"feature flag new-checkout-flow forced to 100% by deploy {ver}, errors follow")],
        "downstream-timeout": [
            ("ERROR", f"context deadline exceeded while calling {dep}"),
            ("WARN", f"{dep} p99 latency {n(4000, 20000)}ms, hedging requests"),
            ("ERROR", f"read tcp: i/o timeout talking to {dep}:443")],
        "cert-expired": [
            ("ERROR", f"SSL: CERTIFICATE_VERIFY_FAILED certificate has expired for {dep}"),
            ("ERROR", f"peer certificate for {dep} is past its notAfter date, refusing the connection")],
        "dns-failure": [
            ("ERROR", f"getaddrinfo ENOTFOUND {dep}.internal"),
            ("ERROR", f"lookup {dep}.internal: server misbehaving"),
            ("WARN", f"SERVFAIL from resolver 10.0.0.2 for {dep}.internal")],
        "quota-exceeded": [
            ("ERROR", f"{dep} responded HTTP 429: rate limit reached, try again later"),
            ("WARN", f"throttled by {dep}: {n(40, 900)} requests rejected in the last minute"),
            ("ERROR", f"RESOURCE_EXHAUSTED: request allowance used up ({dep})")],
        "config-error": [
            ("ERROR", f"failed to parse /etc/{svc}/app.yaml: mapping values are not allowed here at line {n(3, 80)}"),
            ("FATAL", f"startup aborted: unknown option '{r.choice(['pool_sz', 'timeout', 'port'])}' in configuration"),
            ("WARN", "reload rejected, keeping previous configuration")],
    }[cause]


def _signal(cause: str, r: random.Random, svc: str, dep: str, ver: str, step: float, style: str = "classic"):
    pool = []
    if style in ("classic", "mixed"):
        pool += _classic(cause, r, svc, dep, ver, step)
    if style in ("paraphrase", "mixed"):
        pool += _paraphrase(cause, r, svc, dep, ver, step)
    return r.choice(pool)


# Weak, low-count lines that look like another cause. They are not escalating and are
# never the real cause. They sit in the window to give a filter something to be fooled by.
WEAK_DECOYS = [
    ("WARN", "filesystem /var usage 78%, 9100MB free"),
    ("WARN", "heap usage 71% of 2048MB, gc pause 40ms"),
    ("WARN", "resolver timeout after 180ms for redis-cache.internal (retry ok)"),
    ("WARN", "slow query 640ms on table orders"),
    ("WARN", "circuit breaker HALF-OPEN for inventory-svc (probe succeeded)"),
]

BENIGN_WARN = [
    "cache miss ratio {a:.2f} above target 0.20",
    "retry 1/3 succeeded for {dep}",
    "slow request {ms}ms on {path}",
    "deprecated API v1 called by client {cid}",
    "disk usage {pct}% on /var",
]
BENIGN_ERROR = [
    "client closed connection before response (499) on {path}",
    "invalid JSON body from client {cid}: unexpected token",
]


def _noise(r: random.Random, svc: str, base: dt.datetime, minutes: int) -> List[LogLine]:
    lines: List[LogLine] = []
    total = minutes * 60
    for t in range(0, total, 30):
        lines.append(LogLine(_ts(base, t + r.randint(0, 3)), "INFO", svc, f"GET /healthz 200 {r.randint(1, 6)}ms"))
    for t in range(0, total, 12):
        lines.append(LogLine(_ts(base, t + r.randint(0, 9)), "INFO", svc,
                             f"request id={r.randrange(16**6):06x} {r.choice(['GET', 'POST'])} {r.choice(PATHS)} 200 {r.randint(8, 140)}ms user={r.randint(100, 999)}"))
    for t in range(0, total, 240):
        lines.append(LogLine(_ts(base, t + r.randint(0, 60)), "INFO", svc, f"metrics scrape completed in {r.randint(4, 30)}ms"))
    for _ in range(r.randint(18, 30)):
        tpl = r.choice(BENIGN_WARN)
        msg = tpl.format(a=r.uniform(0.21, 0.45), dep=r.choice(DEPENDENCIES), ms=r.randint(300, 900),
                         path=r.choice(PATHS), cid=r.randint(1000, 9999), pct=r.randint(41, 62))
        lines.append(LogLine(_ts(base, r.uniform(0, total)), "WARN", svc, msg))
    for _ in range(r.randint(8, 14)):
        tpl = r.choice(BENIGN_ERROR)
        lines.append(LogLine(_ts(base, r.uniform(0, total)), "ERROR", svc, tpl.format(path=r.choice(PATHS), cid=r.randint(1000, 9999))))
    return lines


def _metrics(r: random.Random, alert_name: str, cause: str) -> Dict[str, float]:
    m = {"service_request_rate": round(r.uniform(40, 220), 1), "service_error_rate_percent": round(r.uniform(0.2, 1.4), 2),
         "service_latency_ms": round(r.uniform(60, 180), 1), "service_memory_usage_percent": round(r.uniform(35, 62), 1),
         "service_disk_usage_percent": round(r.uniform(41, 66), 1), "up": 1.0}
    if alert_name == "High Error Rate":
        m["service_error_rate_percent"] = round(r.uniform(16, 38), 2)
    elif alert_name == "High Latency":
        m["service_latency_ms"] = round(r.uniform(620, 1300), 1)
    elif alert_name == "High Memory Usage":
        m["service_memory_usage_percent"] = round(r.uniform(91, 98), 1)
    elif alert_name == "Disk Almost Full":
        m["service_disk_usage_percent"] = round(r.uniform(92, 100), 1)
    elif alert_name == "Service Down":
        m["up"] = 0.0
        m["service_request_rate"] = 0.0
    return m


WINDOW_MIN = 60
EPOCH = dt.datetime(2026, 11, 14, 9, 0, 0)


GENERIC_FAILURES = [
    "request failed: 500 Internal Server Error {m} {path}",
    "request failed: 503 Service Unavailable {m} {path}",
    "request {m} {path} aborted: upstream error",
]


def make_incident(incident_id: str, seed: int, cause: str, alert_name: Optional[str] = None, service: Optional[str] = None,
                  weak_decoy: bool = False, old_burst: bool = False, style: str = "classic", quiet: bool = False,
                  consequence: bool = False) -> Incident:
    r = random.Random(seed)
    service = service or r.choice(SERVICES)
    alert_name = alert_name or r.choice(CAUSES[cause]["alerts"])
    assert alert_name in CAUSES[cause]["alerts"], (cause, alert_name)
    dep = r.choice([d for d in DEPENDENCIES if d != service])
    ver = f"v{r.randint(2, 5)}.{r.randint(0, 9)}.{r.randint(0, 9)}"
    base = EPOCH + dt.timedelta(days=seed % 25, minutes=seed * 7 % 600)
    fired = base + dt.timedelta(minutes=WINDOW_MIN)

    lines = _noise(r, service, base, WINDOW_MIN)

    # The real signal: ramps up over the 8 minutes before the alert fires. A quiet failure logs only a handful of lines.
    count = r.randint(5, 10) if quiet else r.randint(24, 70)
    signal: List[str] = []
    for i in range(count):
        step = i / count
        offset = (WINDOW_MIN - 8) * 60 + step * 7.5 * 60 + r.uniform(0, 6)
        level, msg = _signal(cause, r, service, dep, ver, step, style)
        lines.append(LogLine(_ts(base, offset), level, service, msg))
        signal.append(msg)

    flags: List[str] = [f"style:{style}"]
    if quiet:
        flags.append("quiet")
    if consequence and cause not in ("bad-deploy", "service-down"):
        # Generic failures that are a consequence of the real fault, with nothing in them that names it.
        flags.append("consequence")
        for _ in range(r.randint(10, 22)):
            lines.append(LogLine(_ts(base, (WINDOW_MIN - r.uniform(0.5, 7)) * 60), "ERROR", service,
                                 r.choice(GENERIC_FAILURES).format(m=r.choice(["GET", "POST"]), path=r.choice(PATHS))))
    if weak_decoy:
        flags.append("weak-decoy")
        for _ in range(r.randint(2, 4)):
            level, msg = r.choice([d for d in WEAK_DECOYS if not _looks_like(cause, d[1])])
            lines.append(LogLine(_ts(base, (WINDOW_MIN - r.uniform(1, 9)) * 60), level, service, msg))
    if old_burst:
        flags.append("old-burst")
        other = r.choice([c for c in CAUSES if c != cause])
        start = r.uniform(6, 14) * 60                      # 46-54 minutes before the alert
        big = quiet          # a quiet real failure after a loud old one is the hard case
        if big:
            flags.append("old-burst-large")
        for i in range(r.randint(40, 90) if big else r.randint(14, 30)):
            level, msg = _signal(other, r, service, dep, ver, i / 20, style)
            lines.append(LogLine(_ts(base, start + r.uniform(0, 150)), level, service, msg))
        lines.append(LogLine(_ts(base, start + 200), "INFO", service, "incident closed by on-call, service recovered"))

    if alert_name == "Service Down":
        # A process that is not running does not log healthy traffic.
        cut = _ts(base, (WINDOW_MIN - 7) * 60)
        lines = [l for l in lines if not (l.level == "INFO" and l.ts > cut)]

    lines.sort(key=lambda x: x.ts)
    alert = dict(ALERTS[alert_name])
    metrics = _metrics(r, alert_name, cause)
    alert.update({"name": alert_name, "value": metrics[alert["metric"]], "fired_at": _ts(fired, 0), "service": service})
    return Incident(incident_id, seed, service, alert, metrics, lines, cause, cause, flags, signal)


def _looks_like(cause: str, msg: str) -> bool:
    key = {"disk-full": "filesystem", "memory-leak": "heap", "dns-failure": "resolver", "db-pool-exhausted": "query",
           "downstream-timeout": "circuit"}.get(cause)
    return bool(key and key in msg)


def _build(prefix: str, seeds: range, flag_cycle: bool = True) -> List[Incident]:
    causes = list(CAUSES)
    out = []
    for i, seed in enumerate(seeds):
        cause = causes[i % len(causes)]
        alerts = CAUSES[cause]["alerts"]
        out.append(make_incident(
            f"{prefix}{i + 1:02d}", seed, cause,
            alert_name=alerts[(i // len(causes)) % len(alerts)],
            service=SERVICES[(i * 3 + 1) % len(SERVICES)],
            weak_decoy=flag_cycle and i % 2 == 0,
            old_burst=flag_cycle and (i // 2) % 2 == 1,
            style=["classic", "paraphrase", "mixed"][i % 3],
            quiet=flag_cycle and i % 4 in (1, 2),
            consequence=flag_cycle and i % 3 != 0,
        ))
    return out


def dev_set() -> List[Incident]:
    """10 incidents, one per cause. Used to build and tune the filter and the retrieval."""
    return _build("D", range(1, 11))


def eval_set() -> List[Incident]:
    """20 incidents, two per cause. Frozen before the final evaluation run."""
    return _build("E", range(100, 120))


def all_incidents() -> Dict[str, Incident]:
    return {i.id: i for i in dev_set() + eval_set()}


def manifest() -> List[dict]:
    return [{"id": i.id, "seed": i.seed, "service": i.service, "alert": i.alert["name"], "cause": i.truth_cause,
             "flags": i.flags, "log_lines": len(i.logs), "sha256_16": i.digest()} for i in dev_set() + eval_set()]


if __name__ == "__main__":
    print(json.dumps(manifest(), indent=1))
