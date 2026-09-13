from __future__ import annotations

import builtins
import importlib
import re
import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.unit


class _MetricChild:
    def __init__(self) -> None:
        self.value = 0.0
        self.observations: list[float] = []

    def inc(self, amount: float = 1.0) -> None:
        self.value += amount

    def dec(self, amount: float = 1.0) -> None:
        self.value -= amount

    def set(self, value: float) -> None:
        self.value = value

    def observe(self, amount: float) -> None:
        self.observations.append(amount)


class _MetricBase:
    def __init__(
        self,
        name: str,
        documentation: str,
        labelnames: list[str] | None = None,
        registry=None,
        multiprocess_mode: str | None = None,
    ) -> None:
        self.name = name
        self.documentation = documentation
        self.labelnames = tuple(labelnames or [])
        self.registry = registry
        self.multiprocess_mode = multiprocess_mode
        self.samples: dict[tuple[tuple[str, str], ...], _MetricChild] = {}
        self.root = _MetricChild()

    def labels(self, **labels: str) -> _MetricChild:
        key = tuple(sorted(labels.items()))
        return self.samples.setdefault(key, _MetricChild())

    def inc(self, amount: float = 1.0) -> None:
        self.root.inc(amount)

    def dec(self, amount: float = 1.0) -> None:
        self.root.dec(amount)

    def observe(self, amount: float) -> None:
        self.root.observe(amount)


class _Counter(_MetricBase):
    pass


class _Histogram(_MetricBase):
    pass


class _Gauge(_MetricBase):
    pass


class _CollectorRegistry:
    def __init__(self, *, auto_describe: bool) -> None:
        self.auto_describe = auto_describe


def _fake_prometheus_client_module() -> types.ModuleType:
    module = types.ModuleType("prometheus_client")
    setattr(module, "Counter", _Counter)
    setattr(module, "Histogram", _Histogram)
    setattr(module, "Gauge", _Gauge)
    setattr(module, "CollectorRegistry", _CollectorRegistry)
    return module


@pytest.fixture(autouse=True)
def reset_metrics_modules() -> Iterator[None]:
    module_names = ("app.core.metrics.prometheus", "app.core.metrics.middleware")
    previous = {name: sys.modules.get(name) for name in module_names}
    try:
        yield
    finally:
        for name in module_names:
            sys.modules.pop(name, None)
        for name, module in previous.items():
            if module is not None:
                sys.modules[name] = module


def _load_metrics_modules(
    monkeypatch: pytest.MonkeyPatch, *, prometheus_client_module: types.ModuleType | None
) -> tuple[types.ModuleType, types.ModuleType]:
    for name in ("app.core.metrics.prometheus", "app.core.metrics.middleware"):
        sys.modules.pop(name, None)

    if prometheus_client_module is not None:
        monkeypatch.setitem(sys.modules, "prometheus_client", prometheus_client_module)
    else:
        monkeypatch.delitem(sys.modules, "prometheus_client", raising=False)
        real_import = builtins.__import__

        def _missing_prometheus_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "prometheus_client":
                raise ImportError("prometheus_client is not installed")
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", _missing_prometheus_import)

    prometheus_module = importlib.import_module("app.core.metrics.prometheus")
    middleware_module = importlib.import_module("app.core.metrics.middleware")
    return prometheus_module, middleware_module


def test_prometheus_metrics_defined_when_dependency_available(monkeypatch: pytest.MonkeyPatch) -> None:
    prometheus_module, _ = _load_metrics_modules(monkeypatch, prometheus_client_module=_fake_prometheus_client_module())

    assert prometheus_module.PROMETHEUS_AVAILABLE is True
    assert prometheus_module.REGISTRY is not None
    assert prometheus_module.requests_total.name == "codex_lb_requests_total"
    assert prometheus_module.request_duration_seconds.name == "codex_lb_request_duration_seconds"
    assert prometheus_module.active_connections.name == "codex_lb_active_connections"
    assert prometheus_module.bridge_instance_mismatch_total.name == "codex_lb_bridge_instance_mismatch_total"
    assert prometheus_module.bridge_instance_mismatch_total.labelnames == ("outcome",)
    assert prometheus_module.continuity_owner_resolution_total.name == "codex_lb_continuity_owner_resolution_total"
    assert prometheus_module.continuity_owner_resolution_total.labelnames == ("surface", "source", "outcome")
    assert prometheus_module.continuity_fail_closed_total.name == "codex_lb_continuity_fail_closed_total"
    assert prometheus_module.continuity_fail_closed_total.labelnames == ("surface", "reason")
    assert prometheus_module.continuity_replay_rejected_total.name == "codex_lb_continuity_replay_rejected_total"
    assert prometheus_module.continuity_replay_rejected_total.labelnames == ("surface", "reason")
    assert prometheus_module.upstream_reasoning_replay_400_total.name == "codex_lb_upstream_reasoning_replay_400_total"
    assert prometheus_module.upstream_reasoning_replay_400_total.labelnames == ()
    assert prometheus_module.account_inflight_leases.name == "codex_lb_account_inflight_leases"
    assert prometheus_module.account_inflight_leases.labelnames == ("account_id", "kind")
    assert prometheus_module.image_requests_total.name == "codex_lb_image_requests_total"
    assert prometheus_module.image_requests_total.labelnames == (
        "route",
        "model",
        "stream",
        "status",
        "outcome",
    )
    assert prometheus_module.image_request_duration_seconds.name == "codex_lb_image_request_duration_seconds"
    assert prometheus_module.image_request_duration_seconds.labelnames == (
        "route",
        "model",
        "stream",
        "status",
        "outcome",
    )


def test_cap_partition_replicas_gauge_uses_livemax_in_multiprocess_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    prometheus_module, _ = _load_metrics_modules(monkeypatch, prometheus_client_module=_fake_prometheus_client_module())

    assert prometheus_module.MULTIPROCESS_MODE is True
    gauge = prometheus_module.cap_partition_replicas
    assert gauge is not None
    assert gauge.name == "codex_lb_cap_partition_replicas"
    # Regression: "max" aggregates dead workers too (mark_process_dead only
    # removes live* gauge files), so a scaled-down worker's stale higher
    # count would be reported forever. "livemax" drops dead workers while
    # still taking the max across live sibling workers.
    assert gauge.multiprocess_mode == "livemax"


def test_cap_partition_replicas_gauge_has_no_multiprocess_mode_in_single_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    prometheus_module, _ = _load_metrics_modules(monkeypatch, prometheus_client_module=_fake_prometheus_client_module())

    assert prometheus_module.MULTIPROCESS_MODE is False
    gauge = prometheus_module.cap_partition_replicas
    assert gauge is not None
    assert gauge.multiprocess_mode is None


@pytest.mark.asyncio
async def test_metrics_middleware_records_request_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    prometheus_module, middleware_module = _load_metrics_modules(
        monkeypatch,
        prometheus_client_module=_fake_prometheus_client_module(),
    )

    app = FastAPI()
    app.add_middleware(middleware_module.MetricsMiddleware, enabled=True)

    @app.get("/v1/chat/completions/123")
    async def tracked_route() -> dict[str, str]:
        return {"status": "ok"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/v1/chat/completions/123")

    assert response.status_code == 200
    request_sample = prometheus_module.requests_total.samples[
        (("method", "GET"), ("path", "/v1/..."), ("status", "200"))
    ]
    duration_sample = prometheus_module.request_duration_seconds.samples[(("method", "GET"), ("path", "/v1/..."))]
    assert request_sample.value == 1.0
    assert len(duration_sample.observations) == 1
    assert prometheus_module.active_connections.root.value == 0.0


@pytest.mark.asyncio
async def test_metrics_middleware_bounds_unmatched_path_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    prometheus_module, middleware_module = _load_metrics_modules(
        monkeypatch,
        prometheus_client_module=_fake_prometheus_client_module(),
    )

    app = FastAPI()
    app.add_middleware(middleware_module.MetricsMiddleware, enabled=True)

    paths = [f"/probe-{index}" for index in range(50)]
    paths.append("/dashboard/settings/profile")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        for path in paths:
            response = await client.get(path)
            assert response.status_code == 404

    path_values = {dict(labels)["path"] for labels in prometheus_module.requests_total.samples}
    assert path_values == {"/other"}


@pytest.mark.asyncio
async def test_metrics_middleware_bounds_primary_proxy_path_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    prometheus_module, middleware_module = _load_metrics_modules(
        monkeypatch,
        prometheus_client_module=_fake_prometheus_client_module(),
    )

    app = FastAPI()
    app.add_middleware(middleware_module.MetricsMiddleware, enabled=True)

    paths = (
        "/backend-api/codex/responses",
        "/backend-api/files/file_abc/uploaded",
        "/backend-api/files/file_xyz/uploaded",
        "/internal/bridge/instance-123",
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        for path in paths:
            response = await client.get(path)
            assert response.status_code == 404

    expected_path_values = {"/backend-api/...", "/internal/..."}
    request_path_values = {dict(labels)["path"] for labels in prometheus_module.requests_total.samples}
    duration_path_values = {dict(labels)["path"] for labels in prometheus_module.request_duration_seconds.samples}
    assert request_path_values == expected_path_values
    assert duration_path_values == expected_path_values


@pytest.mark.asyncio
async def test_metrics_middleware_classifies_mounted_proxy_path_application_relative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prometheus_module, middleware_module = _load_metrics_modules(
        monkeypatch,
        prometheus_client_module=_fake_prometheus_client_module(),
    )

    app = FastAPI()
    middleware = middleware_module.MetricsMiddleware(app, enabled=True)
    cases = (
        ("", "/backend-api/codex/responses"),
        ("/prefix", "/prefix/backend-api/codex/responses"),
    )
    for root_path, path in cases:
        transport = ASGITransport(app=middleware, root_path=root_path)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(path)
        assert response.status_code == 404

    expected_path_values = {"/backend-api/..."}
    request_path_values = {dict(labels)["path"] for labels in prometheus_module.requests_total.samples}
    duration_path_values = {dict(labels)["path"] for labels in prometheus_module.request_duration_seconds.samples}
    assert request_path_values == expected_path_values
    assert duration_path_values == expected_path_values


@pytest.mark.asyncio
async def test_metrics_middleware_normalizes_unknown_method(monkeypatch: pytest.MonkeyPatch) -> None:
    prometheus_module, middleware_module = _load_metrics_modules(
        monkeypatch,
        prometheus_client_module=_fake_prometheus_client_module(),
    )

    app = FastAPI()
    app.add_middleware(middleware_module.MetricsMiddleware, enabled=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.request("BREW", "/unmatched")

    assert response.status_code == 404
    request_sample = prometheus_module.requests_total.samples[
        (("method", "OTHER"), ("path", "/other"), ("status", "404"))
    ]
    duration_sample = prometheus_module.request_duration_seconds.samples[(("method", "OTHER"), ("path", "/other"))]
    assert request_sample.value == 1.0
    assert len(duration_sample.observations) == 1


@pytest.mark.asyncio
async def test_metrics_middleware_noops_without_prometheus_client(monkeypatch: pytest.MonkeyPatch) -> None:
    prometheus_module, middleware_module = _load_metrics_modules(monkeypatch, prometheus_client_module=None)

    app = FastAPI()
    app.add_middleware(middleware_module.MetricsMiddleware, enabled=True)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert prometheus_module.PROMETHEUS_AVAILABLE is False
    assert prometheus_module.requests_total is None


def test_bridge_instance_mismatch_counter_increments(monkeypatch: pytest.MonkeyPatch) -> None:
    prometheus_module, _ = _load_metrics_modules(monkeypatch, prometheus_client_module=_fake_prometheus_client_module())

    assert prometheus_module.PROMETHEUS_AVAILABLE is True
    counter = prometheus_module.bridge_instance_mismatch_total
    assert counter is not None

    fallback_sample = counter.labels(outcome="fallback")
    assert fallback_sample.value == 0.0

    fallback_sample.inc()
    assert fallback_sample.value == 1.0

    fallback_sample.inc()
    assert fallback_sample.value == 2.0


def test_bridge_instance_mismatch_counter_noop_without_prometheus(monkeypatch: pytest.MonkeyPatch) -> None:
    prometheus_module, _ = _load_metrics_modules(monkeypatch, prometheus_client_module=None)

    assert prometheus_module.PROMETHEUS_AVAILABLE is False
    assert prometheus_module.bridge_instance_mismatch_total is None


_REPO_ROOT = Path(__file__).resolve().parents[2]
_OVERFLOW_OBSERVABILITY_DELTA = (
    _REPO_ROOT / "openspec/changes/add-subscription-overflow-model-source/specs/proxy-runtime-observability/spec.md"
)
_PROMETHEUS_MODULE = _REPO_ROOT / "app/core/metrics/prometheus.py"
# Every metric the overflow feature registers: the WP-C1 ``codex_lb_model_source_*`` family and the WP-C2
# ``codex_lb_subscription_overflow_*`` decision counter; the delta must name exactly these, in both directions.
_MODEL_SOURCE_METRIC = re.compile(r"codex_lb_(?:model_source|subscription_overflow)_[a-z_]+")


def test_overflow_observability_delta_names_exactly_the_registered_model_source_metrics() -> None:
    """``openspec validate --strict`` cannot catch a metric-name drift between the normative delta and the registry.

    Design decision 18 renamed the live-pin gauge to dodge the inertness
    ratchet; the delta must name what the module registers, in both directions.
    """

    spec_names = set(_MODEL_SOURCE_METRIC.findall(_OVERFLOW_OBSERVABILITY_DELTA.read_text(encoding="utf-8")))
    registered = set(_MODEL_SOURCE_METRIC.findall(_PROMETHEUS_MODULE.read_text(encoding="utf-8")))

    assert spec_names, "the observability delta names no model-source metrics"
    assert spec_names == registered, {
        "in_spec_only": sorted(spec_names - registered),
        "registered_only": sorted(registered - spec_names),
    }


def test_overflow_decision_metrics_are_registered_in_both_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    """WP-C2 registers the decision counter and the breaker gauge (real client and the ``None`` stub alike)."""

    prometheus_module = importlib.import_module("app.core.metrics.prometheus")
    names = set(prometheus_module.__all__)
    assert {"subscription_overflow_total", "model_source_breaker_state"} <= names

    source = _PROMETHEUS_MODULE.read_text(encoding="utf-8")
    assert 'Counter(\n        "codex_lb_subscription_overflow_total"' in source
    assert '["route", "outcome"]' in source
    assert 'Gauge(\n        "codex_lb_model_source_breaker_state"' in source
    assert "subscription_overflow_total: CounterLike | None = None" in source
    assert "model_source_breaker_state: GaugeLike | None = None" in source

    overflow_module = importlib.import_module("app.modules.proxy.overflow")
    assert overflow_module.OVERFLOW_TOTAL_METRIC == "codex_lb_subscription_overflow_total"
    assert overflow_module.BREAKER_STATE_METRIC == "codex_lb_model_source_breaker_state"
