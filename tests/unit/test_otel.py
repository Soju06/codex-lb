from __future__ import annotations

import asyncio
import builtins
import errno
import json
import logging
import sys
from collections import deque
from types import ModuleType, SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import aiohttp
import anyio
import pytest
from httpx import ASGITransport, AsyncClient

import app.core.tracing.otel as otel
import app.modules.proxy.service as proxy_module
from app.core.audit import service as audit_service_module
from app.core.clients.proxy import ProxyResponseError
from app.core.clients.proxy_websocket import UpstreamResponsesWebSocket
from app.core.config.settings import Settings
from app.core.runtime_logging import JsonFormatter
from app.core.usage import refresh_scheduler as refresh_scheduler_module
from app.core.utils.time import utcnow
from app.db.models import AccountStatus
from app.dependencies import get_proxy_service_for_app
from app.modules.api_keys.service import ApiKeyData
from app.modules.fleet.schemas import FleetRefreshResponse
from app.modules.usage import updater as usage_updater_module

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
async def test_lifespan_drains_actual_audit_and_cancelled_fleet_tasks_before_resource_close(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import app.core.startup as startup_module
    import app.main as main
    from app.core import shutdown as shutdown_state
    from app.core.auth.dependencies import (
        require_dashboard_write_access,
        validate_dashboard_session,
        validate_usage_api_key,
    )
    from app.dependencies import get_accounts_context
    from app.modules.accounts.schemas import AccountImportResponse

    settings = Settings(
        otel_enabled=False,
        otel_exporter_endpoint="",
        metrics_enabled=False,
        shutdown_drain_timeout_seconds=5,
    )
    settings_cache = SimpleNamespace(
        invalidate=AsyncMock(),
        get=AsyncMock(return_value=SimpleNamespace(password_hash=None)),
    )
    rate_limit_cache = SimpleNamespace(invalidate=AsyncMock())
    usage_scheduler = _DummyScheduler()
    api_key_limit_reset_scheduler = _DummyScheduler()
    model_scheduler = _DummyScheduler()
    sticky_scheduler = _DummyScheduler()
    call_order: list[str] = []
    lifespan_entered = asyncio.Event()
    begin_shutdown = asyncio.Event()
    ring_marked_stale = asyncio.Event()
    final_control_plane_drain_started = asyncio.Event()
    audit_write_started = asyncio.Event()
    allow_audit_write = asyncio.Event()
    audit_route_started = asyncio.Event()
    allow_audit_route_finish = asyncio.Event()
    fleet_refresh_started = asyncio.Event()
    allow_fleet_refresh = asyncio.Event()
    fleet_refresh_finished = asyncio.Event()
    audit_write_actions: list[str] = []

    async def _mark_stale(*args, **kwargs) -> None:
        _ = (args, kwargs)
        call_order.append("mark_ring_stale")
        ring_marked_stale.set()

    ring_service = SimpleNamespace(
        register=AsyncMock(),
        mark_stale=AsyncMock(side_effect=_mark_stale),
        unregister=AsyncMock(),
        heartbeat=AsyncMock(),
        list_active=AsyncMock(return_value=[]),
    )

    async def _init_db() -> None:
        call_order.append("init_db")

    def _init_background_db() -> None:
        call_order.append("init_background_db")

    async def _blocked_audit_write(
        action: str,
        actor_ip: str | None,
        details: audit_service_module.AuditDetails | None,
        request_id: str | None,
    ) -> None:
        _ = (action, actor_ip, details, request_id)
        audit_write_actions.append(action)
        audit_write_started.set()
        await allow_audit_write.wait()

    async def _blocked_fleet_refresh(_: list[str] | None) -> FleetRefreshResponse:
        fleet_refresh_started.set()
        await allow_fleet_refresh.wait()
        fleet_refresh_finished.set()
        return FleetRefreshResponse(
            usage_written=False,
            account_count=0,
            attempted_count=0,
            generated_at=utcnow(),
        )

    async def _close_http_client() -> None:
        assert fleet_refresh_finished.is_set()
        assert not shutdown_state.is_control_plane_task_admission_open()
        call_order.append("close_http_client")

    async def _close_db() -> None:
        assert fleet_refresh_finished.is_set()
        assert not shutdown_state.is_control_plane_task_admission_open()
        call_order.append("close_db")

    class _BlockedAccountsService:
        async def import_account(self, raw: bytes) -> AccountImportResponse:
            assert raw == b"{}"
            audit_route_started.set()
            await allow_audit_route_finish.wait()
            return AccountImportResponse(
                account_id="late-audit-account",
                email="late-audit@example.com",
                plan_type="plus",
                status="active",
            )

    fleet_api_key = ApiKeyData(
        id="lifespan-fleet-key",
        name="lifespan fleet key",
        key_prefix="lifespan-fleet",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )

    async def _allow_dashboard_access() -> None:
        return None

    async def _accounts_context_override() -> SimpleNamespace:
        return SimpleNamespace(service=_BlockedAccountsService())

    async def _fleet_api_key_override() -> ApiKeyData:
        return fleet_api_key

    async def _force_in_flight_timeout(*, timeout_seconds: float) -> bool:
        assert timeout_seconds == 5
        assert shutdown_state.get_in_flight() == 2
        return False

    original_control_plane_drain = main._drain_detached_control_plane_tasks

    async def _track_control_plane_drain(timeout_seconds: float) -> None:
        final_control_plane_drain_started.set()
        await original_control_plane_drain(timeout_seconds)

    init_db = AsyncMock()
    init_db.side_effect = _init_db
    init_background_db = Mock(side_effect=_init_background_db)
    init_http_client = AsyncMock()
    close_http_client = AsyncMock(side_effect=_close_http_client)
    close_db = AsyncMock(side_effect=_close_db)

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "get_settings_cache", lambda: settings_cache)
    monkeypatch.setattr(main, "ensure_auto_bootstrap_token", AsyncMock(return_value=None))
    monkeypatch.setattr(main, "get_rate_limit_headers_cache", lambda: rate_limit_cache)
    monkeypatch.setattr(main, "reload_additional_quota_registry", lambda: None)
    monkeypatch.setattr(main, "init_db", init_db)
    monkeypatch.setattr(main, "init_background_db", init_background_db)
    monkeypatch.setattr(main, "init_http_client", init_http_client)
    monkeypatch.setattr(main, "_ensure_bridge_durable_schema_ready", AsyncMock())
    monkeypatch.setattr(main, "verify_encryption_key_fingerprint", AsyncMock(return_value=None))
    monkeypatch.setattr(main, "close_http_client", close_http_client)
    monkeypatch.setattr(main, "close_db", close_db)
    monkeypatch.setattr(audit_service_module, "_write_audit_log", _blocked_audit_write)
    monkeypatch.setattr(main.fleet_api, "_refresh_fleet_usage_with_owned_session", _blocked_fleet_refresh)
    monkeypatch.setattr(main, "build_usage_refresh_scheduler", lambda: usage_scheduler)
    monkeypatch.setattr(main, "build_api_key_limit_reset_scheduler", lambda: api_key_limit_reset_scheduler)
    monkeypatch.setattr(main, "build_model_refresh_scheduler", lambda: model_scheduler)
    monkeypatch.setattr(main, "build_sticky_session_cleanup_scheduler", lambda: sticky_scheduler)
    monkeypatch.setattr(main, "RingMembershipService", lambda session_factory: ring_service)
    monkeypatch.setattr(shutdown_state, "wait_for_in_flight_drain", _force_in_flight_timeout)
    monkeypatch.setattr(main, "_drain_detached_control_plane_tasks", _track_control_plane_drain)
    monkeypatch.setitem(main.app.dependency_overrides, validate_dashboard_session, _allow_dashboard_access)
    monkeypatch.setitem(main.app.dependency_overrides, require_dashboard_write_access, _allow_dashboard_access)
    monkeypatch.setitem(main.app.dependency_overrides, get_accounts_context, _accounts_context_override)
    monkeypatch.setitem(main.app.dependency_overrides, validate_usage_api_key, _fleet_api_key_override)
    caplog.set_level(logging.WARNING, logger=audit_service_module.__name__)

    async def _run_lifespan() -> None:
        async with main.lifespan(main.app):
            await asyncio.sleep(0)
            lifespan_entered.set()
            await begin_shutdown.wait()

    assert audit_service_module._AUDIT_LOG_TASKS == set()
    assert main.fleet_api._BACKGROUND_REFRESH_TASKS == set()
    lifespan_task = asyncio.create_task(_run_lifespan())

    await asyncio.wait_for(lifespan_entered.wait(), timeout=1)
    assert startup_module._startup_complete is True
    assert shutdown_state.is_control_plane_task_admission_open() is True
    assert usage_scheduler.started is True
    assert api_key_limit_reset_scheduler.started is True
    assert model_scheduler.started is True
    assert sticky_scheduler.started is True

    audit_service_module.AuditService.log_async("lifespan_shutdown_test")
    await asyncio.wait_for(audit_write_started.wait(), timeout=1)
    audit_task = next(iter(audit_service_module._AUDIT_LOG_TASKS))

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        fleet_request_task = asyncio.create_task(client.post("/api/fleet/refresh"))
        audit_request_task = asyncio.create_task(
            client.post(
                "/api/accounts/import",
                files={"auth_json": ("auth.json", b"{}", "application/json")},
            )
        )
        await asyncio.wait_for(fleet_refresh_started.wait(), timeout=1)
        await asyncio.wait_for(audit_route_started.wait(), timeout=1)

        assert shutdown_state.get_in_flight() == 2
        assert len(main.fleet_api._BACKGROUND_REFRESH_TASKS) == 1
        fleet_task = next(iter(main.fleet_api._BACKGROUND_REFRESH_TASKS))

        begin_shutdown.set()
        await asyncio.wait_for(ring_marked_stale.wait(), timeout=1)
        await asyncio.wait_for(final_control_plane_drain_started.wait(), timeout=1)

        assert shutdown_state.is_control_plane_task_admission_open() is False
        assert not lifespan_task.done()
        close_http_client.assert_not_awaited()
        close_db.assert_not_awaited()

        fleet_request_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await fleet_request_task
        assert main.fleet_api._BACKGROUND_REFRESH_TASKS == {fleet_task}

        allow_audit_route_finish.set()
        audit_response = await asyncio.wait_for(audit_request_task, timeout=1)
        assert audit_response.status_code == 200
        assert audit_write_actions == ["lifespan_shutdown_test"]
        assert "Audit log task rejected after shutdown admission closed: account_created" in caplog.text

        assert not lifespan_task.done()
        close_http_client.assert_not_awaited()
        close_db.assert_not_awaited()

        allow_audit_write.set()
        await asyncio.wait_for(audit_task, timeout=1)
        await asyncio.sleep(0)
        assert not lifespan_task.done()
        close_http_client.assert_not_awaited()
        close_db.assert_not_awaited()

        allow_fleet_refresh.set()
        await asyncio.wait_for(fleet_task, timeout=1)
        await asyncio.wait_for(lifespan_task, timeout=1)

    init_db.assert_awaited_once()
    init_background_db.assert_called_once()
    init_http_client.assert_awaited_once()
    close_http_client.assert_awaited_once()
    close_db.assert_awaited_once()
    settings_cache.invalidate.assert_awaited_once()
    rate_limit_cache.invalidate.assert_awaited_once()
    assert call_order[:2] == ["init_db", "init_background_db"]
    assert call_order.index("mark_ring_stale") < call_order.index("close_http_client")
    assert call_order.index("mark_ring_stale") < call_order.index("close_db")
    assert shutdown_state.is_control_plane_task_admission_open() is False
    assert audit_service_module._AUDIT_LOG_TASKS == set()
    assert main.fleet_api._BACKGROUND_REFRESH_TASKS == set()
    assert usage_scheduler.stopped is True
    assert api_key_limit_reset_scheduler.stopped is True
    assert model_scheduler.stopped is True
    assert sticky_scheduler.stopped is True


@pytest.mark.asyncio
async def test_lifespan_marks_bridge_membership_stale_on_shutdown(monkeypatch: pytest.MonkeyPatch):
    import app.core.startup as startup_module
    import app.main as main
    from app.core.cache.invalidation import get_cache_invalidation_poller

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
    api_key_limit_reset_scheduler = _DummyScheduler()
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
        list_active=AsyncMock(return_value=[]),
    )
    cache_poller = SimpleNamespace(
        on_invalidation=Mock(),
        prime=AsyncMock(),
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
    monkeypatch.setattr(main, "verify_encryption_key_fingerprint", AsyncMock(return_value=None))
    monkeypatch.setattr(main, "close_http_client", close_http_client)
    monkeypatch.setattr(main, "close_db", close_db)
    monkeypatch.setattr(main, "build_usage_refresh_scheduler", lambda: usage_scheduler)
    monkeypatch.setattr(main, "build_api_key_limit_reset_scheduler", lambda: api_key_limit_reset_scheduler)
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
        stale_threshold_seconds=main.RING_STALE_THRESHOLD_SECONDS,
        grace_seconds=main.RING_STALE_GRACE_SECONDS,
    )
    ring_service.unregister.assert_not_called()
    cache_poller.stop.assert_awaited_once()
    # Shutdown must clear the process-global poller so bump_cache_invalidation
    # is a no-op (not a call through this test's fake) after lifespan exit.
    assert get_cache_invalidation_poller() is None


@pytest.mark.asyncio
async def test_lifespan_shutdown_fails_bridge_capacity_waiter_and_cancels_usage_singleflight(
    monkeypatch: pytest.MonkeyPatch,
):
    import app.core.startup as startup_module
    import app.main as main

    usage_updater_module._clear_usage_refresh_state()

    class _NoopStartUsageScheduler(refresh_scheduler_module.UsageRefreshScheduler):
        async def start(self) -> None:
            return None

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
    usage_scheduler = _NoopStartUsageScheduler(interval_seconds=60, enabled=True)
    api_key_limit_reset_scheduler = _DummyScheduler()
    model_scheduler = _DummyScheduler()
    sticky_scheduler = _DummyScheduler()
    close_http_client = AsyncMock()
    close_db = AsyncMock()
    ring_service = SimpleNamespace(
        register=AsyncMock(),
        mark_stale=AsyncMock(),
        unregister=AsyncMock(),
        heartbeat=AsyncMock(),
        list_active=AsyncMock(return_value=[]),
    )
    cache_poller = SimpleNamespace(
        on_invalidation=Mock(),
        prime=AsyncMock(),
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
    monkeypatch.setattr(main, "verify_encryption_key_fingerprint", AsyncMock(return_value=None))
    monkeypatch.setattr(main, "close_http_client", close_http_client)
    monkeypatch.setattr(main, "close_db", close_db)
    monkeypatch.setattr(main, "build_usage_refresh_scheduler", lambda: usage_scheduler)
    monkeypatch.setattr(main, "build_api_key_limit_reset_scheduler", lambda: api_key_limit_reset_scheduler)
    monkeypatch.setattr(main, "build_model_refresh_scheduler", lambda: model_scheduler)
    monkeypatch.setattr(main, "build_sticky_session_cleanup_scheduler", lambda: sticky_scheduler)
    monkeypatch.setattr(main, "RingMembershipService", lambda session_factory: ring_service)
    monkeypatch.setattr(main, "mark_process_dead", Mock())
    monkeypatch.setattr(
        "app.core.cache.invalidation.CacheInvalidationPoller",
        lambda session_factory: cache_poller,
    )

    app = main.create_app()

    try:
        async with main.lifespan(app):
            await asyncio.sleep(0)
            assert startup_module._startup_complete is True

            service = get_proxy_service_for_app(app)
            existing_key = proxy_module._HTTPBridgeSessionKey("session_header", "sid-capacity-existing", None)
            existing = proxy_module._HTTPBridgeSession(
                key=existing_key,
                headers={},
                affinity=proxy_module._AffinityPolicy(
                    key="sid-capacity-existing",
                    kind=proxy_module.StickySessionKind.CODEX_SESSION,
                ),
                request_model="gpt-5.4",
                account=cast(Any, SimpleNamespace(id="acc-existing", status=AccountStatus.ACTIVE)),
                upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
                upstream_control=proxy_module._WebSocketUpstreamControl(),
                pending_requests=deque(),
                pending_lock=anyio.Lock(),
                response_create_gate=asyncio.Semaphore(1),
                queued_request_count=1,
                last_used_at=1.0,
                idle_ttl_seconds=120.0,
                codex_session=True,
                prewarm_lock=anyio.Lock(),
            )
            service._http_bridge_sessions[existing_key] = existing
            inflight_key = proxy_module._HTTPBridgeSessionKey(
                "session_header",
                "sid-capacity-inflight",
                None,
            )
            inflight_future: asyncio.Future[proxy_module._HTTPBridgeSession] = (
                asyncio.get_running_loop().create_future()
            )
            service._http_bridge_inflight_sessions[inflight_key] = inflight_future

            monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", Mock(return_value=[]))
            monkeypatch.setattr(service, "_http_bridge_pending_count", AsyncMock(return_value=1))
            monkeypatch.setattr(
                proxy_module,
                "_http_bridge_should_wait_for_registration",
                AsyncMock(return_value=False),
            )
            monkeypatch.setattr(proxy_module, "_http_bridge_owner_instance", AsyncMock(return_value="pod-a"))
            monkeypatch.setattr(
                proxy_module,
                "_active_http_bridge_instance_ring",
                AsyncMock(return_value=("pod-a", ("pod-a",))),
            )
            create_http_bridge_session = AsyncMock()
            monkeypatch.setattr(service, "_create_http_bridge_session", create_http_bridge_session)
            monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())
            monkeypatch.setattr(service, "_close_http_bridge_session", AsyncMock())

            capacity_waiter = asyncio.create_task(
                service._get_or_create_http_bridge_session(
                    proxy_module._HTTPBridgeSessionKey("session_header", "sid-capacity-request", None),
                    headers={"x-codex-session-id": "sid-capacity-request"},
                    affinity=proxy_module._AffinityPolicy(
                        key="sid-capacity-request",
                        kind=proxy_module.StickySessionKind.CODEX_SESSION,
                    ),
                    api_key=None,
                    request_model="gpt-5.4",
                    idle_ttl_seconds=120.0,
                    max_sessions=1,
                )
            )
            await asyncio.sleep(0)
            assert not capacity_waiter.done()

            started = asyncio.Event()
            cancelled = asyncio.Event()

            async def factory():
                started.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    cancelled.set()
                    raise

            singleflight_task = asyncio.create_task(
                usage_updater_module._USAGE_REFRESH_SINGLEFLIGHT.run("acc-lifespan-shutdown", factory)
            )
            await started.wait()
        with pytest.raises(ProxyResponseError) as capacity_exc:
            await asyncio.wait_for(capacity_waiter, timeout=0.1)
        assert capacity_exc.value.status_code == 503
        assert capacity_exc.value.payload["error"]["code"] == "upstream_unavailable"
        create_http_bridge_session.assert_not_awaited()

        with pytest.raises(asyncio.CancelledError):
            await singleflight_task
        assert cancelled.is_set()
        assert usage_updater_module._USAGE_REFRESH_SINGLEFLIGHT._inflight == {}
    finally:
        usage_updater_module._clear_usage_refresh_state()


@pytest.mark.asyncio
async def test_lifespan_marks_bridge_membership_stale_for_hostname_shared_ids(
    monkeypatch: pytest.MonkeyPatch,
):
    import app.core.startup as startup_module
    import app.main as main

    monkeypatch.setenv("HOSTNAME", "pod-a")
    settings = Settings(
        otel_enabled=False,
        otel_exporter_endpoint="",
        metrics_enabled=False,
        shutdown_drain_timeout_seconds=0,
        http_responses_session_bridge_instance_id="pod-a",
        http_responses_session_bridge_advertise_base_url="http://pod-a.bridge.default.svc.cluster.local:2455",
    )
    settings_cache = SimpleNamespace(
        invalidate=AsyncMock(),
        get=AsyncMock(return_value=SimpleNamespace(password_hash=None)),
    )
    rate_limit_cache = SimpleNamespace(invalidate=AsyncMock())
    usage_scheduler = _DummyScheduler()
    api_key_limit_reset_scheduler = _DummyScheduler()
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
        list_active=AsyncMock(return_value=[]),
    )
    cache_poller = SimpleNamespace(
        on_invalidation=Mock(),
        prime=AsyncMock(),
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
    monkeypatch.setattr(main, "verify_encryption_key_fingerprint", AsyncMock(return_value=None))
    monkeypatch.setattr(main, "close_http_client", close_http_client)
    monkeypatch.setattr(main, "close_db", close_db)
    monkeypatch.setattr(main, "build_usage_refresh_scheduler", lambda: usage_scheduler)
    monkeypatch.setattr(main, "build_api_key_limit_reset_scheduler", lambda: api_key_limit_reset_scheduler)
    monkeypatch.setattr(main, "build_model_refresh_scheduler", lambda: model_scheduler)
    monkeypatch.setattr(main, "build_sticky_session_cleanup_scheduler", lambda: sticky_scheduler)
    monkeypatch.setattr(main, "RingMembershipService", lambda session_factory: ring_service)
    monkeypatch.setattr(main, "_wait_for_bridge_advertise_endpoint", AsyncMock())
    monkeypatch.setattr(main, "_validate_bridge_advertise_endpoint_for_multi_replica", AsyncMock())
    monkeypatch.setattr(main, "mark_process_dead", Mock())
    monkeypatch.setattr(
        "app.core.cache.invalidation.CacheInvalidationPoller",
        lambda session_factory: cache_poller,
    )

    async with main.lifespan(main.app):
        await asyncio.sleep(0)

    ring_service.mark_stale.assert_awaited_once_with(
        "pod-a",
        stale_threshold_seconds=main.RING_STALE_THRESHOLD_SECONDS,
        grace_seconds=main.RING_STALE_GRACE_SECONDS,
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
    api_key_limit_reset_scheduler = _DummyScheduler()
    model_scheduler = _DummyScheduler()
    sticky_scheduler = _DummyScheduler()
    close_http_client = AsyncMock()
    close_db = AsyncMock()
    ring_service = SimpleNamespace(
        register=AsyncMock(),
        mark_stale=AsyncMock(),
        unregister=AsyncMock(),
        heartbeat=AsyncMock(),
        list_active=AsyncMock(return_value=[]),
    )
    cache_poller = SimpleNamespace(
        on_invalidation=Mock(),
        prime=AsyncMock(),
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
    monkeypatch.setattr(main, "verify_encryption_key_fingerprint", AsyncMock(return_value=None))
    monkeypatch.setattr(main, "close_http_client", close_http_client)
    monkeypatch.setattr(main, "close_db", close_db)
    monkeypatch.setattr(main, "build_usage_refresh_scheduler", lambda: usage_scheduler)
    monkeypatch.setattr(main, "build_api_key_limit_reset_scheduler", lambda: api_key_limit_reset_scheduler)
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
        wait_for_reachable.assert_awaited_once_with(
            "http://pod-a.bridge.default.svc.cluster.local:2455",
            connect_timeout_seconds=settings.upstream_connect_timeout_seconds,
        )
        validate_advertise.assert_awaited_once()
        ring_service.register.assert_awaited_once_with(
            "pod-a",
            endpoint_base_url=None,
        )
        ring_service.heartbeat.assert_awaited_once_with(
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
    api_key_limit_reset_scheduler = _DummyScheduler()
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
    monkeypatch.setattr(main, "build_api_key_limit_reset_scheduler", lambda: api_key_limit_reset_scheduler)
    monkeypatch.setattr(main, "build_model_refresh_scheduler", lambda: model_scheduler)
    monkeypatch.setattr(main, "build_sticky_session_cleanup_scheduler", lambda: sticky_scheduler)
    monkeypatch.setattr(
        main, "_ensure_bridge_durable_schema_ready", AsyncMock(side_effect=RuntimeError("missing schema"))
    )
    monkeypatch.setattr(main, "verify_encryption_key_fingerprint", AsyncMock(return_value=None))

    with pytest.raises(RuntimeError, match="missing schema"):
        async with main.lifespan(main.app):
            pass


@pytest.mark.asyncio
async def test_lifespan_allows_missing_bridge_schema_when_fail_fast_disabled(monkeypatch: pytest.MonkeyPatch):
    import app.core.startup as startup_module
    import app.main as main

    settings = Settings(
        otel_enabled=False,
        otel_exporter_endpoint="",
        metrics_enabled=False,
        shutdown_drain_timeout_seconds=0,
        database_migrations_fail_fast=False,
    )
    settings_cache = SimpleNamespace(
        invalidate=AsyncMock(), get=AsyncMock(return_value=SimpleNamespace(password_hash=None))
    )
    rate_limit_cache = SimpleNamespace(invalidate=AsyncMock())
    usage_scheduler = _DummyScheduler()
    api_key_limit_reset_scheduler = _DummyScheduler()
    model_scheduler = _DummyScheduler()
    sticky_scheduler = _DummyScheduler()
    ring_service = SimpleNamespace(
        register=AsyncMock(),
        mark_stale=AsyncMock(),
        unregister=AsyncMock(),
        heartbeat=AsyncMock(),
        list_active=AsyncMock(return_value=[]),
    )
    cache_poller = SimpleNamespace(on_invalidation=Mock(), prime=AsyncMock(), start=AsyncMock(), stop=AsyncMock())

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
    monkeypatch.setattr(main, "build_api_key_limit_reset_scheduler", lambda: api_key_limit_reset_scheduler)
    monkeypatch.setattr(main, "build_model_refresh_scheduler", lambda: model_scheduler)
    monkeypatch.setattr(main, "build_sticky_session_cleanup_scheduler", lambda: sticky_scheduler)
    monkeypatch.setattr(main, "RingMembershipService", lambda session_factory: ring_service)
    monkeypatch.setattr(main, "_ensure_bridge_durable_schema_ready", AsyncMock(return_value=False))
    monkeypatch.setattr(main, "verify_encryption_key_fingerprint", AsyncMock(return_value=None))
    monkeypatch.setattr(main, "mark_process_dead", Mock())
    monkeypatch.setattr(
        "app.core.cache.invalidation.CacheInvalidationPoller",
        lambda session_factory: cache_poller,
    )

    async with main.lifespan(main.app):
        await asyncio.sleep(0)
        assert startup_module._bridge_durable_schema_ready is False


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
        async def list_active(
            self,
            stale_threshold_seconds: int = main.RING_STALE_THRESHOLD_SECONDS,
            *,
            require_endpoint: bool = False,
        ) -> list[str]:
            del require_endpoint
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
        async def list_active(
            self,
            stale_threshold_seconds: int = main.RING_STALE_THRESHOLD_SECONDS,
            *,
            require_endpoint: bool = False,
        ) -> list[str]:
            del require_endpoint
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
        async def list_active(
            self,
            stale_threshold_seconds: int = main.RING_STALE_THRESHOLD_SECONDS,
            *,
            require_endpoint: bool = False,
        ) -> list[str]:
            del require_endpoint
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
        async def list_active(
            self,
            stale_threshold_seconds: int = main.RING_STALE_THRESHOLD_SECONDS,
            *,
            require_endpoint: bool = False,
        ) -> list[str]:
            del require_endpoint
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
        async def list_active(
            self,
            stale_threshold_seconds: int = main.RING_STALE_THRESHOLD_SECONDS,
            *,
            require_endpoint: bool = False,
        ) -> list[str]:
            del require_endpoint
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
