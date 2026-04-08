from __future__ import annotations

import builtins
import errno
import json
import logging
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
    settings_cache = SimpleNamespace(invalidate=AsyncMock())
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
    monkeypatch.setattr(main, "get_rate_limit_headers_cache", lambda: rate_limit_cache)
    monkeypatch.setattr(main, "reload_additional_quota_registry", lambda: None)
    monkeypatch.setattr(main, "init_db", init_db)
    monkeypatch.setattr(main, "init_background_db", init_background_db)
    monkeypatch.setattr(main, "init_http_client", init_http_client)
    monkeypatch.setattr(main, "close_http_client", close_http_client)
    monkeypatch.setattr(main, "close_db", close_db)
    monkeypatch.setattr(main, "build_usage_refresh_scheduler", lambda: usage_scheduler)
    monkeypatch.setattr(main, "build_model_refresh_scheduler", lambda: model_scheduler)
    monkeypatch.setattr(main, "build_sticky_session_cleanup_scheduler", lambda: sticky_scheduler)
    monkeypatch.setattr(main, "RingMembershipService", lambda session_factory: ring_service)

    async with main.lifespan(main.app):
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
    settings_cache = SimpleNamespace(invalidate=AsyncMock())
    rate_limit_cache = SimpleNamespace(invalidate=AsyncMock())
    usage_scheduler = _DummyScheduler()
    model_scheduler = _DummyScheduler()
    sticky_scheduler = _DummyScheduler()
    close_http_client = AsyncMock()
    close_db = AsyncMock()
    register = AsyncMock()

    async def _register(instance_id: str, *, endpoint_base_url: str | None = None) -> None:
        assert startup_module._startup_complete is False
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
    monkeypatch.setattr(main, "get_rate_limit_headers_cache", lambda: rate_limit_cache)
    monkeypatch.setattr(main, "reload_additional_quota_registry", lambda: None)
    monkeypatch.setattr(main, "init_db", AsyncMock())
    monkeypatch.setattr(main, "init_background_db", Mock())
    monkeypatch.setattr(main, "init_http_client", AsyncMock())
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


def test_metrics_bind_failure_is_only_benign_in_multiprocess_mode(monkeypatch: pytest.MonkeyPatch):
    import app.main as main

    monkeypatch.setattr(main, "MULTIPROCESS_MODE", False)
    assert main._is_benign_metrics_bind_failure(SystemExit(1)) is False
    assert main._is_benign_metrics_bind_failure(OSError(errno.EADDRINUSE, "in use")) is False

    monkeypatch.setattr(main, "MULTIPROCESS_MODE", True)
    assert main._is_benign_metrics_bind_failure(SystemExit(1)) is True
    assert main._is_benign_metrics_bind_failure(OSError(errno.EADDRINUSE, "in use")) is True


def test_local_api_port_uses_port_env(monkeypatch: pytest.MonkeyPatch):
    import app.main as main

    monkeypatch.setenv("PORT", "3765")

    assert main._local_api_port() == 3765


def test_local_api_port_falls_back_for_invalid_env(monkeypatch: pytest.MonkeyPatch):
    import app.main as main

    monkeypatch.setenv("PORT", "not-a-port")
    monkeypatch.setattr(main.sys, "argv", ["uvicorn", "app.main:app", "--port", "4123"])

    assert main._local_api_port() == 4123


def test_local_api_port_supports_equals_style_argv(monkeypatch: pytest.MonkeyPatch):
    import app.main as main

    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setattr(main.sys, "argv", ["uvicorn", "app.main:app", "--port=4124"])

    assert main._local_api_port() == 4124


def test_local_api_port_falls_back_to_default_when_no_valid_port_source(monkeypatch: pytest.MonkeyPatch):
    import app.main as main

    monkeypatch.setenv("PORT", "not-a-port")
    monkeypatch.setattr(main.sys, "argv", ["uvicorn", "app.main:app", "--port", "bad"])

    assert main._local_api_port() == 2455


@pytest.mark.asyncio
async def test_validate_bridge_advertise_endpoint_rejects_shared_hostname():
    import app.main as main

    class _RingReader:
        async def list_active(self) -> list[str]:
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
        async def list_active(self) -> list[str]:
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
