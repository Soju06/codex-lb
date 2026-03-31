from __future__ import annotations

import builtins
import json
import logging
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import app.core.tracing.otel as otel
from app.core.runtime_logging import JsonFormatter

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
        def __init__(self) -> None:
            self.processors: list[FakeBatchSpanProcessor] = []

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

    settings = SimpleNamespace(
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
