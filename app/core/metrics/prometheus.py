from __future__ import annotations

import os
from importlib import import_module
from typing import Protocol


class CollectorRegistryLike(Protocol):
    pass


class CounterLike(Protocol):
    def inc(self, amount: float = 1) -> None: ...
    def labels(self, *args: str, **kwargs: str) -> "CounterLike": ...


class GaugeLike(Protocol):
    def inc(self, amount: float = 1) -> None: ...
    def dec(self, amount: float = 1) -> None: ...
    def set(self, value: float) -> None: ...
    def labels(self, *args: str, **kwargs: str) -> "GaugeLike": ...


class HistogramLike(Protocol):
    def observe(self, amount: float) -> None: ...
    def labels(self, *args: str, **kwargs: str) -> "HistogramLike": ...


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

    _gauge_kwargs: dict[str, str] = {}
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
    bridge_prompt_cache_locality_miss_total = Counter(
        "codex_lb_bridge_prompt_cache_locality_miss_total",
        "Total prompt-cache bridge locality misses tolerated via gateway-safe handling",
        registry=REGISTRY,
    )
    bridge_soft_local_rebind_total = Counter(
        "codex_lb_bridge_soft_local_rebind_total",
        "Total soft-affinity bridge sessions rebound locally on a non-owner instance",
        registry=REGISTRY,
    )
    bridge_owner_forward_total = Counter(
        "codex_lb_bridge_owner_forward_total",
        "Total bridge owner forwards by outcome",
        ["outcome"],
        registry=REGISTRY,
    )
    bridge_durable_recover_total = Counter(
        "codex_lb_bridge_durable_recover_total",
        "Total durable bridge recoveries by path",
        ["path"],
        registry=REGISTRY,
    )
    bridge_same_account_takeover_total = Counter(
        "codex_lb_bridge_same_account_takeover_total",
        "Total same-account takeover outcomes",
        ["outcome"],
        registry=REGISTRY,
    )
    bridge_reattach_total = Counter(
        "codex_lb_bridge_reattach_total",
        "Total bridge reattach outcomes by path",
        ["path", "outcome"],
        registry=REGISTRY,
    )
    bridge_first_turn_timeout_total = Counter(
        "codex_lb_bridge_first_turn_timeout_total",
        "Total first-turn bridge timeouts during upstream connect",
        registry=REGISTRY,
    )
    bridge_drain_recovery_allowed_total = Counter(
        "codex_lb_bridge_drain_recovery_allowed_total",
        "Total continuity recoveries allowed while bridge drain is active",
        registry=REGISTRY,
    )
    bridge_owner_mismatch_total = Counter(
        "codex_lb_bridge_owner_mismatch_total",
        "Total bridge owner mismatches by key strength",
        ["strength"],
        registry=REGISTRY,
    )
    bridge_local_rebind_total = Counter(
        "codex_lb_bridge_local_rebind_total",
        "Total bridge local rebinds by reason",
        ["reason"],
        registry=REGISTRY,
    )
    bridge_forward_latency_seconds = Histogram(
        "codex_lb_bridge_forward_latency_seconds",
        "Bridge owner forward latency",
        registry=REGISTRY,
    )
    bridge_public_contract_error_total = Counter(
        "codex_lb_bridge_public_contract_error_total",
        "Total public /responses contract violations by kind",
        ["kind"],
        registry=REGISTRY,
    )

    def make_scrape_registry() -> CollectorRegistryLike:
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
    REGISTRY: CollectorRegistryLike | None = None
    requests_total: CounterLike | None = None
    request_duration_seconds: HistogramLike | None = None
    upstream_requests_total: CounterLike | None = None
    upstream_request_duration_seconds: HistogramLike | None = None
    active_connections: GaugeLike | None = None
    rate_limit_hits_total: CounterLike | None = None
    circuit_breaker_state: GaugeLike | None = None
    accounts_total: GaugeLike | None = None
    bridge_instance_mismatch_total: CounterLike | None = None
    bridge_prompt_cache_locality_miss_total: CounterLike | None = None
    bridge_soft_local_rebind_total: CounterLike | None = None
    bridge_owner_forward_total: CounterLike | None = None
    bridge_durable_recover_total: CounterLike | None = None
    bridge_same_account_takeover_total: CounterLike | None = None
    bridge_reattach_total: CounterLike | None = None
    bridge_first_turn_timeout_total: CounterLike | None = None
    bridge_drain_recovery_allowed_total: CounterLike | None = None
    bridge_owner_mismatch_total: CounterLike | None = None
    bridge_local_rebind_total: CounterLike | None = None
    bridge_forward_latency_seconds: HistogramLike | None = None
    bridge_public_contract_error_total: CounterLike | None = None

    def make_scrape_registry() -> None:
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
    "bridge_forward_latency_seconds",
    "bridge_durable_recover_total",
    "bridge_drain_recovery_allowed_total",
    "bridge_first_turn_timeout_total",
    "bridge_local_rebind_total",
    "bridge_owner_forward_total",
    "bridge_owner_mismatch_total",
    "bridge_public_contract_error_total",
    "bridge_prompt_cache_locality_miss_total",
    "bridge_reattach_total",
    "bridge_same_account_takeover_total",
    "bridge_soft_local_rebind_total",
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
