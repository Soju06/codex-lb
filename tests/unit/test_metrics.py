from __future__ import annotations

import builtins
import importlib
import sys
import types
from collections.abc import Iterator

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

    def observe(self, amount: float) -> None:
        self.observations.append(amount)


class _MetricBase:
    def __init__(self, name: str, documentation: str, labelnames: list[str] | None = None, registry=None) -> None:
        self.name = name
        self.documentation = documentation
        self.labelnames = tuple(labelnames or [])
        self.registry = registry
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
