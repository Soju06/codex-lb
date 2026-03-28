from __future__ import annotations

from importlib import import_module
from typing import Any

try:
    prometheus_client = import_module("prometheus_client")
except ImportError:
    prometheus_client = None


PROMETHEUS_AVAILABLE = prometheus_client is not None


if PROMETHEUS_AVAILABLE:
    CollectorRegistry = getattr(prometheus_client, "CollectorRegistry")
    Counter = getattr(prometheus_client, "Counter")
    Gauge = getattr(prometheus_client, "Gauge")
    Histogram = getattr(prometheus_client, "Histogram")

    REGISTRY = CollectorRegistry(auto_describe=True)

    requests_total = Counter(
        "codex_lb_requests_total",
        "Total HTTP requests",
        ["method", "path", "status"],
        registry=REGISTRY,
    )
    request_duration_seconds = Histogram(
        "codex_lb_request_duration_seconds",
        "HTTP request duration",
        ["method", "path"],
        registry=REGISTRY,
    )
    upstream_requests_total = Counter(
        "codex_lb_upstream_requests_total",
        "Total upstream requests",
        ["account_id", "status"],
        registry=REGISTRY,
    )
    upstream_request_duration_seconds = Histogram(
        "codex_lb_upstream_request_duration_seconds",
        "Upstream request duration",
        registry=REGISTRY,
    )
    active_connections = Gauge(
        "codex_lb_active_connections",
        "Active HTTP connections",
        registry=REGISTRY,
    )
    rate_limit_hits_total = Counter(
        "codex_lb_rate_limit_hits_total",
        "Rate limit hits",
        ["type"],
        registry=REGISTRY,
    )
    circuit_breaker_state = Gauge(
        "codex_lb_circuit_breaker_state",
        "Circuit breaker state (0=closed, 1=open, 2=half-open)",
        ["service"],
        registry=REGISTRY,
    )
    accounts_total = Gauge(
        "codex_lb_accounts_total",
        "Total accounts by status",
        ["status"],
        registry=REGISTRY,
    )
else:
    REGISTRY: Any = None
    requests_total: Any = None
    request_duration_seconds: Any = None
    upstream_requests_total: Any = None
    upstream_request_duration_seconds: Any = None
    active_connections: Any = None
    rate_limit_hits_total: Any = None
    circuit_breaker_state: Any = None
    accounts_total: Any = None


__all__ = [
    "PROMETHEUS_AVAILABLE",
    "REGISTRY",
    "active_connections",
    "accounts_total",
    "circuit_breaker_state",
    "prometheus_client",
    "rate_limit_hits_total",
    "request_duration_seconds",
    "requests_total",
    "upstream_request_duration_seconds",
    "upstream_requests_total",
]
