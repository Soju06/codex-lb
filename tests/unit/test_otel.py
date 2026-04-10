from __future__ import annotations

import asyncio
import builtins
import errno
import json
import logging
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest

import app.core.tracing.otel as otel
from app.core.config.settings import Settings
from app.core.runtime_logging import JsonFormatter
from app.modules.proxy.ring_membership import RING_STALE_GRACE_SECONDS, RING_STALE_THRESHOLD_SECONDS

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def reset_otel_state(monkeypatch: pytest.MonkeyPatch):
    otel._otel_initialized = False
    for name in list(sys.modules):
        if name == "opentelemetry" or name.startswith("opentelemetry."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    yield
    otel._otel_initialized = False


def _install_fake_otel(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(
        provider=None,
        exporter_endpoint=None,
        fastapi_instrumented=0,
        aiohttp_instrumented=0,
        sqlalchemy_instrumented=0,
        resource_attributes=None,
    )

    class FakeSpanContext:
        def __init__(self, trace_id: int, span_id: int, is_valid: bool = True) -> None:
            self.trace_id = trace_id
            self.span_id = span_id
            self.is_valid = is_valid

    class FakeSpan:
        def __init__(self, context: FakeSpanContext) -> None:
            self._context = context

        def get_span_context(self) -> FakeSpanContext:
            return self._context

    class FakeTracerProvider:
        def __init__(self, *, resource: object | None = None) -> None:
            self.processors: list[FakeBatchSpanProcessor] = []
            self.resource = resource
            state.resource_attributes = getattr(resource, "attributes", None)

        def add_span_processor(self, processor: FakeBatchSpanProcessor) -> None:
            self.processors.append(processor)

    class FakeBatchSpanProcessor:
        def __init__(self, exporter: object) -> None:
            self.exporter = exporter

    class FakeOTLPSpanExporter:
        def __init__(self, endpoint: str) -> None:
            state.exporter_endpoint = endpoint
            self.endpoint = endpoint

    class FakeFastAPIInstrumentor:
        def instrument(self) -> None:
            state.fastapi_instrumented += 1

    class FakeAioHttpClientInstrumentor:
        def instrument(self) -> None:
            state.aiohttp_instrumented += 1

    class FakeSQLAlchemyInstrumentor:
        def instrument(self) -> None:
            state.sqlalchemy_instrumented += 1

    trace_module = ModuleType("opentelemetry.trace")
    setattr(trace_module, "_current_span", FakeSpan(FakeSpanContext(trace_id=0x1234, span_id=0x5678)))

    def set_tracer_provider(provider: FakeTracerProvider) -> None:
        state.provider = provider

    def get_current_span() -> FakeSpan:
        return getattr(trace_module, "_current_span")

    setattr(trace_module, "set_tracer_provider", set_tracer_provider)
    setattr(trace_module, "get_current_span", get_current_span)

    opentelemetry_module = ModuleType("opentelemetry")
    opentelemetry_module.__path__ = []
    setattr(opentelemetry_module, "trace", trace_module)

    sdk_module = ModuleType("opentelemetry.sdk")
    sdk_module.__path__ = []
    sdk_resources_module = ModuleType("opentelemetry.sdk.resources")

    class FakeResource:
        def __init__(self, attributes: dict[str, str]) -> None:
            self.attributes = attributes

        @classmethod
        def create(cls, attributes: dict[str, str]) -> "FakeResource":
            return cls(attributes)

    setattr(sdk_resources_module, "Resource", FakeResource)
    setattr(sdk_resources_module, "SERVICE_NAME", "service.name")
    sdk_trace_module = ModuleType("opentelemetry.sdk.trace")
    setattr(sdk_trace_module, "TracerProvider", FakeTracerProvider)
    sdk_trace_export_module = ModuleType("opentelemetry.sdk.trace.export")
    setattr(sdk_trace_export_module, "BatchSpanProcessor", FakeBatchSpanProcessor)

    exporter_module = ModuleType("opentelemetry.exporter")
    exporter_module.__path__ = []
    exporter_otlp_module = ModuleType("opentelemetry.exporter.otlp")
    exporter_otlp_module.__path__ = []
    exporter_otlp_proto_module = ModuleType("opentelemetry.exporter.otlp.proto")
    exporter_otlp_proto_module.__path__ = []
    exporter_otlp_proto_grpc_module = ModuleType("opentelemetry.exporter.otlp.proto.grpc")
    exporter_otlp_proto_grpc_module.__path__ = []
    exporter_trace_module = ModuleType("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
    setattr(exporter_trace_module, "OTLPSpanExporter", FakeOTLPSpanExporter)

    instrumentation_module = ModuleType("opentelemetry.instrumentation")
    instrumentation_module.__path__ = []
    instrumentation_fastapi_module = ModuleType("opentelemetry.instrumentation.fastapi")
    setattr(instrumentation_fastapi_module, "FastAPIInstrumentor", FakeFastAPIInstrumentor)
    instrumentation_aiohttp_module = ModuleType("opentelemetry.instrumentation.aiohttp_client")
    setattr(instrumentation_aiohttp_module, "AioHttpClientInstrumentor", FakeAioHttpClientInstrumentor)
    instrumentation_sqlalchemy_module = ModuleType("opentelemetry.instrumentation.sqlalchemy")
    setattr(instrumentation_sqlalchemy_module, "SQLAlchemyInstrumentor", FakeSQLAlchemyInstrumentor)

    modules = {
        "opentelemetry": opentelemetry_module,
        "opentelemetry.trace": trace_module,
        "opentelemetry.sdk": sdk_module,
        "opentelemetry.sdk.resources": sdk_resources_module,
        "opentelemetry.sdk.trace": sdk_trace_module,
        "opentelemetry.sdk.trace.export": sdk_trace_export_module,
        "opentelemetry.exporter": exporter_module,
        "opentelemetry.exporter.otlp": exporter_otlp_module,
        "opentelemetry.exporter.otlp.proto": exporter_otlp_proto_module,
        "opentelemetry.exporter.otlp.proto.grpc": exporter_otlp_proto_grpc_module,
        "opentelemetry.exporter.otlp.proto.grpc.trace_exporter": exporter_trace_module,
        "opentelemetry.instrumentation": instrumentation_module,
        "opentelemetry.instrumentation.fastapi": instrumentation_fastapi_module,
        "opentelemetry.instrumentation.aiohttp_client": instrumentation_aiohttp_module,
        "opentelemetry.instrumentation.sqlalchemy": instrumentation_sqlalchemy_module,
    }

    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    return state


def test_init_tracing_returns_false_when_opentelemetry_is_unavailable(monkeypatch: pytest.MonkeyPatch):
    original_import = builtins.__import__

    def raising_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "opentelemetry" or name.startswith("opentelemetry."):
            raise ImportError("missing opentelemetry")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", raising_import)

    assert otel.init_tracing() is False
    assert otel.is_initialized() is False


def test_init_tracing_returns_true_when_opentelemetry_modules_are_available(monkeypatch: pytest.MonkeyPatch):
    state = _install_fake_otel(monkeypatch)

    assert otel.init_tracing(service_name="codex-lb", endpoint="http://collector:4317") is True
    assert otel.is_initialized() is True
    assert state.provider is not None
    assert len(state.provider.processors) == 1
    assert state.exporter_endpoint == "http://collector:4317"
    assert state.resource_attributes == {"service.name": "codex-lb"}
    assert state.fastapi_instrumented == 1
    assert state.aiohttp_instrumented == 1
    assert state.sqlalchemy_instrumented == 1


def test_get_current_trace_id_returns_none_when_opentelemetry_is_inactive(monkeypatch: pytest.MonkeyPatch):
    original_import = builtins.__import__

    def raising_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "opentelemetry" or name.startswith("opentelemetry."):
            raise ImportError("missing opentelemetry")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", raising_import)

    assert otel.get_current_trace_id() is None
    assert otel.get_current_span_id() is None


def test_json_formatter_includes_trace_and_span_ids_when_available(monkeypatch: pytest.MonkeyPatch):
    _install_fake_otel(monkeypatch)

    record = logging.LogRecord(
        name="test.module",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )

    parsed = json.loads(JsonFormatter().format(record))

    assert parsed["trace_id"] == "00000000000000000000000000001234"
    assert parsed["span_id"] == "0000000000005678"


class _DummyScheduler:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


@pytest.mark.asyncio
async def test_lifespan_runs_normally_when_otel_is_disabled(monkeypatch: pytest.MonkeyPatch):
    import app.core.startup as startup_module
    import app.main as main

    settings = Settings(
        otel_enabled=False,
        otel_exporter_endpoint="",
        metrics_enabled=False,
        shutdown_drain_timeout_seconds=0,
    )
    settings_cache = SimpleNamespace(
        invalidate=AsyncMock(),
        get=AsyncMock(return_value=SimpleNamespace(password_hash=None)),
    )
    rate_limit_cache = SimpleNamespace(invalidate=AsyncMock())
    usage_scheduler = _DummyScheduler()
    model_scheduler = _DummyScheduler()
    sticky_scheduler = _DummyScheduler()
    ring_service = SimpleNamespace(
        register=AsyncMock(),
        mark_stale=AsyncMock(),
        unregister=AsyncMock(),
        heartbeat=AsyncMock(),
    )
    call_order: list[str] = []

    async def _init_db() -> None:
        call_order.append("init_db")

    def _init_background_db() -> None:
        call_order.append("init_background_db")

    init_db = AsyncMock()
    init_db.side_effect = _init_db
    init_background_db = Mock(side_effect=_init_background_db)
    init_http_client = AsyncMock()
    close_http_client = AsyncMock()
    close_db = AsyncMock()

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "get_settings_cache", lambda: settings_cache)
    monkeypatch.setattr(main, "ensure_auto_bootstrap_token", AsyncMock(return_value=None))
    monkeypatch.setattr(main, "get_rate_limit_headers_cache", lambda: rate_limit_cache)
    monkeypatch.setattr(main, "reload_additional_quota_registry", lambda: None)
    monkeypatch.setattr(main, "init_db", init_db)
    monkeypatch.setattr(main, "init_background_db", init_background_db)
    monkeypatch.setattr(main, "init_http_client", init_http_client)
    monkeypatch.setattr(main, "_ensure_bridge_durable_schema_ready", AsyncMock())
    monkeypatch.setattr(main, "close_http_client", close_http_client)
    monkeypatch.setattr(main, "close_db", close_db)
    monkeypatch.setattr(main, "build_usage_refresh_scheduler", lambda: usage_scheduler)
    monkeypatch.setattr(main, "build_model_refresh_scheduler", lambda: model_scheduler)
    monkeypatch.setattr(main, "build_sticky_session_cleanup_scheduler", lambda: sticky_scheduler)
    monkeypatch.setattr(main, "RingMembershipService", lambda session_factory: ring_service)

    async with main.lifespan(main.app):
        await asyncio.sleep(0)
        assert startup_module._startup_complete is True
        assert usage_scheduler.started is True
        assert model_scheduler.started is True
        assert sticky_scheduler.started is True

    init_db.assert_awaited_once()
    init_background_db.assert_called_once()
    init_http_client.assert_awaited_once()
    close_http_client.assert_awaited_once()
    close_db.assert_awaited_once()
    settings_cache.invalidate.assert_awaited_once()
    rate_limit_cache.invalidate.assert_awaited_once()
    assert call_order[:2] == ["init_db", "init_background_db"]
    assert usage_scheduler.stopped is True
    assert model_scheduler.stopped is True
    assert sticky_scheduler.stopped is True


@pytest.mark.asyncio
async def test_lifespan_marks_bridge_membership_stale_on_shutdown(monkeypatch: pytest.MonkeyPatch):
    import app.core.startup as startup_module
    import app.main as main

    settings = Settings(
        otel_enabled=False,
        otel_exporter_endpoint="",
        metrics_enabled=False,
        shutdown_drain_timeout_seconds=0,
        http_responses_session_bridge_instance_id="pod-a",
    )
    settings_cache = SimpleNamespace(
        invalidate=AsyncMock(),
        get=AsyncMock(return_value=SimpleNamespace(password_hash=None)),
    )
    rate_limit_cache = SimpleNamespace(invalidate=AsyncMock())
    usage_scheduler = _DummyScheduler()
    model_scheduler = _DummyScheduler()
    sticky_scheduler = _DummyScheduler()
    close_http_client = AsyncMock()
    close_db = AsyncMock()
    register = AsyncMock()

    async def _register(instance_id: str, *, endpoint_base_url: str | None = None) -> None:
        assert startup_module._startup_complete is True
        await register(instance_id, endpoint_base_url=endpoint_base_url)

    ring_service = SimpleNamespace(
        register=AsyncMock(side_effect=_register),
        mark_stale=AsyncMock(),
        unregister=AsyncMock(),
        heartbeat=AsyncMock(),
    )
    cache_poller = SimpleNamespace(
        on_invalidation=Mock(),
        start=AsyncMock(),
        stop=AsyncMock(),
    )

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "get_settings_cache", lambda: settings_cache)
    monkeypatch.setattr(main, "ensure_auto_bootstrap_token", AsyncMock(return_value=None))
    monkeypatch.setattr(main, "get_rate_limit_headers_cache", lambda: rate_limit_cache)
    monkeypatch.setattr(main, "reload_additional_quota_registry", lambda: None)
    monkeypatch.setattr(main, "init_db", AsyncMock())
    monkeypatch.setattr(main, "init_background_db", Mock())
    monkeypatch.setattr(main, "init_http_client", AsyncMock())
    monkeypatch.setattr(main, "_ensure_bridge_durable_schema_ready", AsyncMock())
    monkeypatch.setattr(main, "close_http_client", close_http_client)
    monkeypatch.setattr(main, "close_db", close_db)
    monkeypatch.setattr(main, "build_usage_refresh_scheduler", lambda: usage_scheduler)
    monkeypatch.setattr(main, "build_model_refresh_scheduler", lambda: model_scheduler)
    monkeypatch.setattr(main, "build_sticky_session_cleanup_scheduler", lambda: sticky_scheduler)
    monkeypatch.setattr(main, "RingMembershipService", lambda session_factory: ring_service)
    wait_for_reachable = AsyncMock()
    monkeypatch.setattr(main, "_wait_for_bridge_advertise_endpoint", wait_for_reachable)
    validate_advertise = AsyncMock()
    monkeypatch.setattr(main, "_validate_bridge_advertise_endpoint_for_multi_replica", validate_advertise)
    monkeypatch.setattr(main, "mark_process_dead", Mock())
    monkeypatch.setattr(
        "app.core.cache.invalidation.CacheInvalidationPoller",
        lambda session_factory: cache_poller,
    )

    async with main.lifespan(main.app):
        await asyncio.sleep(0)
        assert startup_module._startup_complete is True

    register.assert_awaited_once_with("pod-a", endpoint_base_url=None)
    wait_for_reachable.assert_not_awaited()
    validate_advertise.assert_not_awaited()
    ring_service.heartbeat.assert_not_awaited()
    ring_service.mark_stale.assert_awaited_once_with(
        "pod-a",
        stale_threshold_seconds=RING_STALE_THRESHOLD_SECONDS,
        grace_seconds=RING_STALE_GRACE_SECONDS,
    )
    ring_service.unregister.assert_not_called()


@pytest.mark.asyncio
async def test_lifespan_registers_bridge_without_waiting_for_advertise_self_probe(
    monkeypatch: pytest.MonkeyPatch,
):
    import app.core.startup as startup_module
    import app.main as main

    settings = Settings(
        otel_enabled=False,
        otel_exporter_endpoint="",
        metrics_enabled=False,
        shutdown_drain_timeout_seconds=0,
        http_responses_session_bridge_instance_id="pod-a",
        http_responses_session_bridge_advertise_base_url="http://pod-a.bridge.default.svc.cluster.local:2455",
    )
    settings_cache = SimpleNamespace(invalidate=AsyncMock())
    rate_limit_cache = SimpleNamespace(invalidate=AsyncMock())
    usage_scheduler = _DummyScheduler()
    model_scheduler = _DummyScheduler()
    sticky_scheduler = _DummyScheduler()
    close_http_client = AsyncMock()
    close_db = AsyncMock()
    ring_service = SimpleNamespace(
        register=AsyncMock(),
        mark_stale=AsyncMock(),
        unregister=AsyncMock(),
        heartbeat=AsyncMock(),
    )
    cache_poller = SimpleNamespace(
        on_invalidation=Mock(),
        start=AsyncMock(),
        stop=AsyncMock(),
    )
    wait_for_reachable = AsyncMock()
    validate_advertise = AsyncMock()

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "get_settings_cache", lambda: settings_cache)
    monkeypatch.setattr(main, "ensure_auto_bootstrap_token", AsyncMock(return_value=None))
    monkeypatch.setattr(main, "get_rate_limit_headers_cache", lambda: rate_limit_cache)
    monkeypatch.setattr(main, "reload_additional_quota_registry", lambda: None)
    monkeypatch.setattr(main, "init_db", AsyncMock())
    monkeypatch.setattr(main, "init_background_db", Mock())
    monkeypatch.setattr(main, "init_http_client", AsyncMock())
    monkeypatch.setattr(main, "_ensure_bridge_durable_schema_ready", AsyncMock())
    monkeypatch.setattr(main, "close_http_client", close_http_client)
    monkeypatch.setattr(main, "close_db", close_db)
    monkeypatch.setattr(main, "build_usage_refresh_scheduler", lambda: usage_scheduler)
    monkeypatch.setattr(main, "build_model_refresh_scheduler", lambda: model_scheduler)
    monkeypatch.setattr(main, "build_sticky_session_cleanup_scheduler", lambda: sticky_scheduler)
    monkeypatch.setattr(main, "RingMembershipService", lambda session_factory: ring_service)
    monkeypatch.setattr(main, "_wait_for_bridge_advertise_endpoint", wait_for_reachable)
    monkeypatch.setattr(main, "_validate_bridge_advertise_endpoint_for_multi_replica", validate_advertise)
    monkeypatch.setattr(main, "mark_process_dead", Mock())
    monkeypatch.setattr(
        "app.core.cache.invalidation.CacheInvalidationPoller",
        lambda session_factory: cache_poller,
    )

    async with main.lifespan(main.app):
        assert startup_module._startup_complete is True
        await asyncio.sleep(0)
        wait_for_reachable.assert_not_awaited()
        validate_advertise.assert_awaited_once()
        ring_service.register.assert_awaited_once_with(
            "pod-a",
            endpoint_base_url="http://pod-a.bridge.default.svc.cluster.local:2455",
        )
        assert startup_module._startup_complete is True


def test_metrics_bind_failure_is_only_benign_in_multiprocess_mode(monkeypatch: pytest.MonkeyPatch):
    import app.main as main

    monkeypatch.setattr(main, "MULTIPROCESS_MODE", False)
    assert main._is_benign_metrics_bind_failure(SystemExit(1)) is False
    assert main._is_benign_metrics_bind_failure(OSError(errno.EADDRINUSE, "in use")) is False

    monkeypatch.setattr(main, "MULTIPROCESS_MODE", True)
    assert main._is_benign_metrics_bind_failure(SystemExit(1)) is True
    assert main._is_benign_metrics_bind_failure(OSError(errno.EADDRINUSE, "in use")) is True


@pytest.mark.asyncio
async def test_lifespan_fails_fast_when_bridge_durable_schema_is_missing(monkeypatch: pytest.MonkeyPatch):
    import app.main as main

    settings = Settings(
        otel_enabled=False,
        otel_exporter_endpoint="",
        metrics_enabled=False,
        shutdown_drain_timeout_seconds=0,
    )
    settings_cache = SimpleNamespace(invalidate=AsyncMock())
    rate_limit_cache = SimpleNamespace(invalidate=AsyncMock())
    usage_scheduler = _DummyScheduler()
    model_scheduler = _DummyScheduler()
    sticky_scheduler = _DummyScheduler()

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "get_settings_cache", lambda: settings_cache)
    monkeypatch.setattr(main, "ensure_auto_bootstrap_token", AsyncMock(return_value=None))
    monkeypatch.setattr(main, "get_rate_limit_headers_cache", lambda: rate_limit_cache)
    monkeypatch.setattr(main, "reload_additional_quota_registry", lambda: None)
    monkeypatch.setattr(main, "init_db", AsyncMock())
    monkeypatch.setattr(main, "init_background_db", Mock())
    monkeypatch.setattr(main, "init_http_client", AsyncMock())
    monkeypatch.setattr(main, "close_http_client", AsyncMock())
    monkeypatch.setattr(main, "close_db", AsyncMock())
    monkeypatch.setattr(main, "build_usage_refresh_scheduler", lambda: usage_scheduler)
    monkeypatch.setattr(main, "build_model_refresh_scheduler", lambda: model_scheduler)
    monkeypatch.setattr(main, "build_sticky_session_cleanup_scheduler", lambda: sticky_scheduler)
    monkeypatch.setattr(
        main, "_ensure_bridge_durable_schema_ready", AsyncMock(side_effect=RuntimeError("missing schema"))
    )

    with pytest.raises(RuntimeError, match="missing schema"):
        async with main.lifespan(main.app):
            pass


def test_local_api_port_uses_port_env(monkeypatch: pytest.MonkeyPatch):
    import app.main as main

    monkeypatch.setenv("PORT", "3765")

    assert main._local_api_port() == 3765


def test_local_api_port_falls_back_for_invalid_env(monkeypatch: pytest.MonkeyPatch):
    import app.main as main

    monkeypatch.setenv("PORT", "not-a-port")
    monkeypatch.setattr(main.sys, "argv", ["uvicorn", "app.main:app", "--port", "4123"])

    assert main._local_api_port() == 4123


@pytest.mark.asyncio
async def test_wait_for_bridge_advertise_endpoint_probes_configured_url(monkeypatch: pytest.MonkeyPatch):
    import app.main as main

    seen: dict[str, object] = {}

    class _FakeResponse:
        status = 200

        async def __aenter__(self) -> "_FakeResponse":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    class _FakeSession:
        def __init__(self, *args, **kwargs) -> None:
            seen["timeout"] = kwargs.get("timeout")
            seen["trust_env"] = kwargs.get("trust_env")

        async def __aenter__(self) -> "_FakeSession":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        def get(self, url: str, *, ssl: bool | None = None) -> _FakeResponse:
            seen["url"] = url
            seen["ssl"] = ssl
            return _FakeResponse()

    monkeypatch.setattr(main.aiohttp, "ClientSession", _FakeSession)

    await main._wait_for_bridge_advertise_endpoint(
        "http://pod-a.bridge.default.svc.cluster.local:2455",
        connect_timeout_seconds=3.0,
    )

    assert seen["url"] == "http://pod-a.bridge.default.svc.cluster.local:2455/health/live"
    assert seen["ssl"] is None
    assert seen["trust_env"] is False


@pytest.mark.asyncio
async def test_wait_for_bridge_advertise_endpoint_uses_default_tls_verification_for_https_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.main as main

    seen: dict[str, object] = {}

    class _FakeResponse:
        status = 200

        async def __aenter__(self) -> "_FakeResponse":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    class _FakeSession:
        async def __aenter__(self) -> "_FakeSession":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        def get(self, url: str, *, ssl: bool | None = None) -> _FakeResponse:
            seen["url"] = url
            seen["ssl"] = ssl
            return _FakeResponse()

    monkeypatch.setattr(main.aiohttp, "ClientSession", lambda *args, **kwargs: _FakeSession())

    await main._wait_for_bridge_advertise_endpoint(
        "https://pod-a.bridge.default.svc.cluster.local:2455",
        connect_timeout_seconds=3.0,
    )

    assert seen["url"] == "https://pod-a.bridge.default.svc.cluster.local:2455/health/live"
    assert seen["ssl"] is None


@pytest.mark.asyncio
async def test_wait_for_bridge_advertise_endpoint_raises_after_bounded_retry_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.main as main

    current_time = 0.0
    attempts = 0

    def _monotonic() -> float:
        return current_time

    async def _sleep(delay: float) -> None:
        nonlocal current_time
        current_time += delay

    class _FakeSession:
        async def __aenter__(self) -> "_FakeSession":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        def get(self, url: str, *, ssl: bool | None = None):
            nonlocal attempts
            attempts += 1
            raise aiohttp.ClientConnectionError("unreachable")

    monkeypatch.setattr(main.time, "monotonic", _monotonic)
    monkeypatch.setattr(main.asyncio, "sleep", _sleep)
    monkeypatch.setattr(main.aiohttp, "ClientSession", lambda *args, **kwargs: _FakeSession())

    with pytest.raises(RuntimeError, match="did not become reachable"):
        await main._wait_for_bridge_advertise_endpoint(
            "http://pod-a.bridge.default.svc.cluster.local:2455",
            connect_timeout_seconds=3.0,
        )

    assert attempts >= 3
    assert current_time >= 5.0


def test_local_api_port_supports_equals_style_argv(monkeypatch: pytest.MonkeyPatch):
    import app.main as main

    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setattr(main.sys, "argv", ["uvicorn", "app.main:app", "--port=4124"])

    assert main._local_api_port() == 4124


def test_local_api_port_falls_back_to_default_when_no_valid_port_source(monkeypatch: pytest.MonkeyPatch):
    import app.main as main

    monkeypatch.setenv("PORT", "not-a-port")
    monkeypatch.setattr(main.sys, "argv", ["uvicorn", "app.main:app", "--port", "bad"])

    assert main._local_api_port() is None


@pytest.mark.asyncio
async def test_wait_for_bridge_advertise_endpoint_requires_known_local_port_without_advertise_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.main as main

    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setattr(main.sys, "argv", ["gunicorn", "app.main:app"])

    with pytest.raises(RuntimeError, match="Cannot determine local bridge listener port"):
        await main._wait_for_bridge_advertise_endpoint(None, connect_timeout_seconds=3.0)


@pytest.mark.asyncio
async def test_validate_bridge_advertise_endpoint_rejects_shared_hostname():
    import app.main as main

    class _RingReader:
        async def list_active(self, stale_threshold_seconds: int = main.RING_STALE_THRESHOLD_SECONDS) -> list[str]:
            return ["instance-a"]

    settings = Settings(
        http_responses_session_bridge_instance_id="instance-a",
        http_responses_session_bridge_advertise_base_url="http://instance-a.internal.local:2455",
    )

    await main._validate_bridge_advertise_endpoint_for_multi_replica(
        svc=_RingReader(),
        settings=settings,
        instance_id="instance-a",
        endpoint_base_url=settings.http_responses_session_bridge_advertise_base_url,
    )


@pytest.mark.asyncio
async def test_validate_bridge_advertise_endpoint_allows_loopback_for_single_replica():
    import app.main as main

    class _RingReader:
        async def list_active(self, stale_threshold_seconds: int = main.RING_STALE_THRESHOLD_SECONDS) -> list[str]:
            return ["instance-a"]

    settings = Settings(
        http_responses_session_bridge_instance_id="instance-a",
        http_responses_session_bridge_advertise_base_url="http://127.0.0.1:2455",
    )

    await main._validate_bridge_advertise_endpoint_for_multi_replica(
        svc=_RingReader(),
        settings=settings,
        instance_id="instance-a",
        endpoint_base_url=settings.http_responses_session_bridge_advertise_base_url,
    )


@pytest.mark.asyncio
async def test_validate_bridge_advertise_endpoint_rejects_loopback_when_peer_exists():
    import app.main as main

    class _RingReader:
        async def list_active(self, stale_threshold_seconds: int = main.RING_STALE_THRESHOLD_SECONDS) -> list[str]:
            return ["instance-a", "instance-b"]

    settings = Settings(
        http_responses_session_bridge_instance_id="instance-a",
        http_responses_session_bridge_advertise_base_url="http://127.0.0.1:2455",
    )

    with pytest.raises(RuntimeError):
        await main._validate_bridge_advertise_endpoint_for_multi_replica(
            svc=_RingReader(),
            settings=settings,
            instance_id="instance-a",
            endpoint_base_url=settings.http_responses_session_bridge_advertise_base_url,
        )


@pytest.mark.asyncio
async def test_validate_bridge_advertise_endpoint_ignores_stale_grace_peer_for_loopback():
    import app.main as main

    seen: dict[str, int] = {}

    class _RingReader:
        async def list_active(self, stale_threshold_seconds: int = main.RING_STALE_THRESHOLD_SECONDS) -> list[str]:
            seen["threshold"] = stale_threshold_seconds
            if stale_threshold_seconds <= main.RING_HEARTBEAT_INTERVAL_SECONDS:
                return ["instance-a"]
            return ["instance-a", "instance-old"]

    settings = Settings(
        http_responses_session_bridge_instance_id="instance-a",
        http_responses_session_bridge_advertise_base_url="http://127.0.0.1:2455",
    )

    await main._validate_bridge_advertise_endpoint_for_multi_replica(
        svc=_RingReader(),
        settings=settings,
        instance_id="instance-a",
        endpoint_base_url=settings.http_responses_session_bridge_advertise_base_url,
    )

    assert seen["threshold"] == main.RING_HEARTBEAT_INTERVAL_SECONDS


@pytest.mark.asyncio
async def test_validate_bridge_advertise_endpoint_rejects_loopback_for_multi_replica_intent():
    import app.main as main

    class _RingReader:
        async def list_active(self, stale_threshold_seconds: int = main.RING_STALE_THRESHOLD_SECONDS) -> list[str]:
            return ["instance-a"]

    settings = Settings.model_construct(
        http_responses_session_bridge_instance_id="instance-a",
        http_responses_session_bridge_instance_ring=["instance-a", "instance-b"],
        http_responses_session_bridge_advertise_base_url="http://127.0.0.1:2455",
    )

    with pytest.raises(RuntimeError):
        await main._validate_bridge_advertise_endpoint_for_multi_replica(
            svc=_RingReader(),
            settings=settings,
            instance_id="instance-a",
            endpoint_base_url=settings.http_responses_session_bridge_advertise_base_url,
        )
