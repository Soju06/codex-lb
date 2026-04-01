from __future__ import annotations

import os
from importlib import import_module
from typing import Any

try:
    prometheus_client = import_module("prometheus_client")
except ImportError:
    prometheus_client = None


PROMETHEUS_AVAILABLE = prometheus_client is not None
MULTIPROCESS_MODE = bool(os.environ.get("PROMETHEUS_MULTIPROC_DIR"))


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

    _gauge_kwargs: dict[str, Any] = {}
    if MULTIPROCESS_MODE:
        _gauge_kwargs["multiprocess_mode"] = "livesum"

    active_connections = Gauge(
        "codex_lb_active_connections",
        "Active HTTP connections",
        registry=REGISTRY,
        **_gauge_kwargs,
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
        **({"multiprocess_mode": "liveall"} if MULTIPROCESS_MODE else {}),
    )
    accounts_total = Gauge(
        "codex_lb_accounts_total",
        "Total accounts by status",
        ["status"],
        registry=REGISTRY,
        **({"multiprocess_mode": "liveall"} if MULTIPROCESS_MODE else {}),
    )
    bridge_instance_mismatch_total = Counter(
        "codex_lb_bridge_instance_mismatch_total",
        "Total bridge instance mismatches handled via graceful fallback",
        ["outcome"],
        registry=REGISTRY,
    )

    def make_scrape_registry() -> Any:
        if MULTIPROCESS_MODE:
            _multiprocess = import_module("prometheus_client.multiprocess")
            registry = CollectorRegistry()
            _multiprocess.MultiProcessCollector(registry)
            return registry
        return REGISTRY

    def mark_process_dead() -> None:
        if MULTIPROCESS_MODE:
            try:
                _multiprocess = import_module("prometheus_client.multiprocess")
                _multiprocess.mark_process_dead(os.getpid())
            except (ImportError, AttributeError):
                pass

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
    bridge_instance_mismatch_total: Any = None

    def make_scrape_registry() -> Any:
        return None

    def mark_process_dead() -> None:
        pass


__all__ = [
    "MULTIPROCESS_MODE",
    "PROMETHEUS_AVAILABLE",
    "REGISTRY",
    "active_connections",
    "accounts_total",
    "bridge_instance_mismatch_total",
    "circuit_breaker_state",
    "make_scrape_registry",
    "mark_process_dead",
    "prometheus_client",
    "rate_limit_hits_total",
    "request_duration_seconds",
    "requests_total",
    "upstream_request_duration_seconds",
    "upstream_requests_total",
]
