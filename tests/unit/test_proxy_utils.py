from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Protocol, Self, cast
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest
from aiohttp.client_reqrep import RequestInfo
from fastapi import WebSocket
from starlette.requests import Request
from starlette.responses import StreamingResponse

import app.core.clients.proxy as proxy_module
from app.core.clients.proxy import _build_upstream_headers, filter_inbound_headers
from app.core.config.settings import Settings
from app.core.crypto import TokenEncryptor
from app.core.errors import openai_error
from app.core.openai.models import OpenAIResponsePayload
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.resilience.circuit_breaker import CircuitState
from app.core.types import JsonValue
from app.core.utils.request_id import get_request_id, reset_request_id, set_request_id
from app.core.utils.sse import parse_sse_data_json
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.modules.accounts import auth_manager as auth_manager_module
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy import api as proxy_api
from app.modules.proxy import request_policy as proxy_request_policy
from app.modules.proxy import service as proxy_service
from app.modules.proxy.load_balancer import AccountSelection
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository

pytestmark = pytest.mark.unit


def _assert_proxy_response_error(exc: BaseException) -> proxy_module.ProxyResponseError:
    assert isinstance(exc, proxy_module.ProxyResponseError)
    return exc


def _proxy_error_code(exc: proxy_module.ProxyResponseError) -> str | None:
    return exc.payload["error"].get("code")


def _proxy_error_message(exc: proxy_module.ProxyResponseError) -> str | None:
    return exc.payload["error"].get("message")


def test_filter_inbound_headers_strips_auth_and_account():
    headers = {
        "Authorization": "Bearer x",
        "chatgpt-account-id": "acc_1",
        "Content-Encoding": "gzip",
        "Content-Type": "application/json",
        "X-Request-Id": "req_1",
    }
    filtered = filter_inbound_headers(headers)
    assert "Authorization" not in filtered
    assert "chatgpt-account-id" not in filtered
    assert filtered["Content-Encoding"] == "gzip"
    assert filtered["Content-Type"] == "application/json"
    assert filtered["X-Request-Id"] == "req_1"


def test_filter_inbound_headers_strips_proxy_identity_headers():
    headers = {
        "X-Forwarded-For": "1.2.3.4",
        "X-Forwarded-Proto": "https",
        "X-Real-IP": "1.2.3.4",
        "Forwarded": "for=1.2.3.4;proto=https",
        "CF-Connecting-IP": "1.2.3.4",
        "CF-Ray": "ray123",
        "True-Client-IP": "1.2.3.4",
        "User-Agent": "codex-test",
        "Accept": "text/event-stream",
    }

    filtered = filter_inbound_headers(headers)

    assert "X-Forwarded-For" not in filtered
    assert "X-Forwarded-Proto" not in filtered
    assert "X-Real-IP" not in filtered
    assert "Forwarded" not in filtered
    assert "CF-Connecting-IP" not in filtered
    assert "CF-Ray" not in filtered
    assert "True-Client-IP" not in filtered
    assert filtered["User-Agent"] == "codex-test"
    assert filtered["Accept"] == "text/event-stream"


def test_build_upstream_headers_overrides_auth():
    inbound = {"X-Request-Id": "req_1"}
    headers = _build_upstream_headers(inbound, "token", "acc_2")
    assert headers["Authorization"] == "Bearer token"
    assert headers["chatgpt-account-id"] == "acc_2"
    assert headers["Accept"] == "text/event-stream"
    assert headers["Content-Type"] == "application/json"


def test_build_upstream_headers_accept_override():
    inbound = {}
    headers = _build_upstream_headers(inbound, "token", None, accept="application/json")
    assert headers["Accept"] == "application/json"


def test_apply_api_key_enforcement_overrides_service_tier_aliases_to_priority():
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hello",
            "input": [],
            "service_tier": "default",
        }
    )
    api_key = proxy_service.ApiKeyData(
        id="key_1",
        name="service-tier-key",
        key_prefix="sk-clb-test",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier="priority",
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )

    proxy_request_policy.apply_api_key_enforcement(payload, api_key)

    assert payload.service_tier == "priority"


def _build_registry_with_model(slug: str, efforts: list[str]):
    from app.core.openai.model_registry import (
        ModelRegistry,
        ModelRegistrySnapshot,
        ReasoningLevel,
        UpstreamModel,
    )

    upstream = UpstreamModel(
        slug=slug,
        display_name=slug,
        description="",
        context_window=128000,
        input_modalities=("text",),
        supported_reasoning_levels=tuple(ReasoningLevel(effort=e, description="") for e in efforts),
        default_reasoning_level=efforts[1] if len(efforts) > 1 else None,
        supports_reasoning_summaries=False,
        support_verbosity=False,
        default_verbosity=None,
        prefer_websockets=True,
        supports_parallel_tool_calls=True,
        supported_in_api=True,
        minimal_client_version=None,
        priority=0,
        available_in_plans=frozenset({"pro"}),
    )
    snapshot = ModelRegistrySnapshot(
        models={slug: upstream},
        model_plans={slug: frozenset({"pro"})},
        plan_models={"pro": frozenset({slug})},
        fetched_at=0.0,
    )
    registry = ModelRegistry()
    registry._snapshot = snapshot
    return registry


def test_normalize_unsupported_reasoning_effort_rewrites_minimal_to_low(caplog):
    from app.core.openai.requests import ResponsesReasoning

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.5",
            "instructions": "hello",
            "input": [],
        }
    )
    payload.reasoning = ResponsesReasoning(effort="minimal")
    registry = _build_registry_with_model("gpt-5.5", ["low", "medium", "high", "xhigh"])

    with caplog.at_level(logging.INFO, logger="app.modules.proxy.request_policy"):
        proxy_request_policy.normalize_unsupported_reasoning_effort(payload, registry=registry)

    assert payload.reasoning is not None
    assert payload.reasoning.effort == "low"
    assert any("reasoning_effort_normalized" in record.message for record in caplog.records)


def test_normalize_unsupported_reasoning_effort_falls_back_to_low_without_registry():
    from app.core.openai.model_registry import ModelRegistry
    from app.core.openai.requests import ResponsesReasoning

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-unknown",
            "instructions": "hello",
            "input": [],
        }
    )
    payload.reasoning = ResponsesReasoning(effort="MINIMAL")

    proxy_request_policy.normalize_unsupported_reasoning_effort(payload, registry=ModelRegistry())

    assert payload.reasoning is not None
    assert payload.reasoning.effort == "low"


def test_normalize_unsupported_reasoning_effort_preserves_supported_effort():
    from app.core.openai.requests import ResponsesReasoning

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.5",
            "instructions": "hello",
            "input": [],
        }
    )
    payload.reasoning = ResponsesReasoning(effort="high")
    registry = _build_registry_with_model("gpt-5.5", ["low", "medium", "high", "xhigh"])

    proxy_request_policy.normalize_unsupported_reasoning_effort(payload, registry=registry)

    assert payload.reasoning.effort == "high"


def test_apply_api_key_enforcement_normalizes_minimal_without_api_key():
    from app.core.openai.requests import ResponsesReasoning

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.5",
            "instructions": "hello",
            "input": [],
        }
    )
    payload.reasoning = ResponsesReasoning(effort="minimal")

    proxy_request_policy.apply_api_key_enforcement(payload, None)

    assert payload.reasoning is not None
    assert payload.reasoning.effort == "low"


class _RingMembershipStub:
    def __init__(self, members: list[str]) -> None:
        self.members = members

    async def list_active(self, stale_threshold_seconds: int = 120, *, require_endpoint: bool = False) -> list[str]:
        del stale_threshold_seconds, require_endpoint
        return list(self.members)


class _ObservedCounter:
    def __init__(self) -> None:
        self.samples: list[dict[str, object]] = []

    def labels(self, **labels: str):
        sample: dict[str, object] = {"labels": dict(labels), "value": 0.0}
        self.samples.append(sample)

        def inc(amount: float = 1.0) -> None:
            sample["value"] = cast(float, sample["value"]) + amount

        return SimpleNamespace(inc=inc)


@pytest.mark.anyio
async def test_owner_instance_uses_rendezvous_hash() -> None:
    settings = Settings(
        http_responses_session_bridge_instance_id="pod-a",
        http_responses_session_bridge_instance_ring=["pod-a", "pod-b", "pod-c", "pod-d", "pod-e"],
    )
    ring_membership = _RingMembershipStub(["pod-a", "pod-b", "pod-c", "pod-d", "pod-e"])

    owners_before: dict[str, str | None] = {}
    for index in range(1000):
        key = proxy_service._HTTPBridgeSessionKey("prompt_cache_key", f"k-{index}", None)
        owners_before[key.affinity_key] = await proxy_service._http_bridge_owner_instance(
            key,
            settings,
            cast(proxy_service.RingMembershipService, ring_membership),
        )

    ring_membership.members = ["pod-a", "pod-b", "pod-c", "pod-d", "pod-e", "pod-f"]
    moved = 0
    for index in range(1000):
        key = proxy_service._HTTPBridgeSessionKey("prompt_cache_key", f"k-{index}", None)
        owner_after = await proxy_service._http_bridge_owner_instance(
            key,
            settings,
            cast(proxy_service.RingMembershipService, ring_membership),
        )
        if owners_before[key.affinity_key] != owner_after:
            moved += 1

    assert moved / 1000 <= 0.2


@pytest.mark.anyio
async def test_ring_raises_on_db_error() -> None:
    settings = Settings(
        http_responses_session_bridge_instance_id="pod-a",
        http_responses_session_bridge_instance_ring=["pod-a", "pod-b", "pod-c"],
    )
    ring_membership = AsyncMock()
    ring_membership.list_active.side_effect = RuntimeError("db unavailable")

    key = proxy_service._HTTPBridgeSessionKey("prompt_cache_key", "k-fallback", None)
    with pytest.raises(RuntimeError, match="db unavailable"):
        await proxy_service._http_bridge_owner_instance(
            key,
            settings,
            cast(proxy_service.RingMembershipService, ring_membership),
        )


@pytest.mark.asyncio
async def test_resolve_websocket_previous_response_owner_records_request_log_source(monkeypatch, caplog):
    request_logs = _RequestLogsRecorder()
    request_logs.response_owner_by_id[("resp_prev_owner_metric", None, "turn_scope_owner_metric")] = "acc_owner_prev"
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    counter = _ObservedCounter()

    monkeypatch.setattr(proxy_service, "PROMETHEUS_AVAILABLE", True)
    monkeypatch.setattr(proxy_service, "continuity_owner_resolution_total", counter, raising=False)
    caplog.set_level(logging.INFO, logger="app.modules.proxy.service")

    owner = await service._resolve_websocket_previous_response_owner(
        previous_response_id="resp_prev_owner_metric",
        api_key=None,
        session_id="turn_scope_owner_metric",
        surface="websocket",
    )

    assert owner == "acc_owner_prev"
    assert "continuity_owner_resolution surface=websocket source=request_logs outcome=hit" in caplog.text
    assert "previous_response_id=sha256:" in caplog.text
    assert "session_id=sha256:" in caplog.text
    assert "resp_prev_owner_metric" not in caplog.text
    assert "turn_scope_owner_metric" not in caplog.text
    assert counter.samples == [
        {
            "labels": {"surface": "websocket", "source": "request_logs", "outcome": "hit"},
            "value": 1.0,
        }
    ]


@pytest.mark.asyncio
async def test_resolve_websocket_previous_response_owner_fail_closed_records_metric_and_log(monkeypatch, caplog):
    request_logs = _RequestLogsRecorder()
    request_logs.lookup_error = RuntimeError("lookup unavailable")
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    resolution_counter = _ObservedCounter()
    fail_closed_counter = _ObservedCounter()

    monkeypatch.setattr(proxy_service, "PROMETHEUS_AVAILABLE", True)
    monkeypatch.setattr(proxy_service, "continuity_owner_resolution_total", resolution_counter, raising=False)
    monkeypatch.setattr(proxy_service, "continuity_fail_closed_total", fail_closed_counter, raising=False)
    caplog.set_level(logging.WARNING, logger="app.modules.proxy.service")

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service._resolve_websocket_previous_response_owner(
            previous_response_id="resp_prev_owner_metric_fail",
            api_key=None,
            session_id="turn_scope_owner_metric_fail",
            surface="websocket",
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.payload["error"]["code"] == "upstream_unavailable"
    assert "continuity_owner_resolution surface=websocket source=request_logs outcome=fail_closed" in caplog.text
    assert "continuity_fail_closed surface=websocket reason=owner_lookup_failed" in caplog.text
    assert "resp_prev_owner_metric_fail" not in caplog.text
    assert "turn_scope_owner_metric_fail" not in caplog.text
    assert resolution_counter.samples == [
        {
            "labels": {"surface": "websocket", "source": "request_logs", "outcome": "fail_closed"},
            "value": 1.0,
        }
    ]
    assert fail_closed_counter.samples == [
        {
            "labels": {"surface": "websocket", "reason": "owner_lookup_failed"},
            "value": 1.0,
        }
    ]


def test_build_upstream_websocket_headers_strip_accept_and_content_type_case_insensitively():
    headers = proxy_module._build_upstream_websocket_headers(
        {
            "accept": "text/event-stream",
            "content-type": "application/json",
            "User-Agent": "codex-test",
        },
        "token",
        "acc_2",
    )

    assert all(key.lower() != "accept" for key in headers)
    assert all(key.lower() != "content-type" for key in headers)
    assert headers["Authorization"] == "Bearer token"
    assert headers["chatgpt-account-id"] == "acc_2"
    assert headers["User-Agent"] == "codex-test"


def test_build_upstream_websocket_headers_strip_hop_by_hop_headers_and_connection_tokens():
    headers = proxy_module._build_upstream_websocket_headers(
        {
            "Connection": "keep-alive, Upgrade, X-Handshake-Debug",
            "Keep-Alive": "timeout=5",
            "Upgrade": "websocket",
            "Transfer-Encoding": "chunked",
            "Proxy-Connection": "keep-alive",
            "X-Handshake-Debug": "1",
            "User-Agent": "codex-test",
        },
        "token",
        "acc_2",
    )

    assert "Connection" not in headers
    assert "Keep-Alive" not in headers
    assert "Upgrade" not in headers
    assert "Transfer-Encoding" not in headers
    assert "Proxy-Connection" not in headers
    assert "X-Handshake-Debug" not in headers
    assert headers["Authorization"] == "Bearer token"
    assert headers["chatgpt-account-id"] == "acc_2"
    assert headers["User-Agent"] == "codex-test"


@pytest.mark.asyncio
async def test_stream_responses_returns_before_first_upstream_event(monkeypatch):
    async def skip_limits(*args, **kwargs):
        del args, kwargs
        return None

    monkeypatch.setattr(proxy_api, "_enforce_request_limits", skip_limits)

    async def stream_responses(*args, **kwargs):
        del args, kwargs
        await asyncio.sleep(10.0)
        yield 'data: {"type":"response.completed","response":{"id":"resp_slow","status":"completed"}}\n\n'

    context = SimpleNamespace(
        service=SimpleNamespace(
            rate_limit_headers=AsyncMock(return_value={}),
            stream_responses=stream_responses,
        )
    )
    request = Request({"type": "http", "method": "POST", "path": "/v1/responses", "headers": []})
    payload = ResponsesRequest(model="gpt-5.1", instructions="test", input="hello")

    response = await asyncio.wait_for(
        proxy_api._stream_responses(
            request,
            payload,
            context=cast(proxy_api.ProxyContext, context),
            api_key=None,
        ),
        timeout=0.2,
    )

    assert isinstance(response, StreamingResponse)


@pytest.mark.asyncio
async def test_stream_responses_streams_post_startup_proxy_error_as_sse(monkeypatch):
    async def skip_limits(*args, **kwargs):
        del args, kwargs
        return None

    monkeypatch.setattr(proxy_api, "_enforce_request_limits", skip_limits)

    async def stream_responses(*args, **kwargs):
        del args, kwargs
        await asyncio.sleep(0.1)
        raise proxy_module.ProxyResponseError(
            429,
            openai_error("rate_limit_exceeded", "opportunistic burn window closed"),
        )
        yield ""

    context = SimpleNamespace(
        service=SimpleNamespace(
            rate_limit_headers=AsyncMock(return_value={"X-RateLimit-Limit": "1"}),
            stream_responses=stream_responses,
        )
    )
    request = Request({"type": "http", "method": "POST", "path": "/v1/responses", "headers": []})
    payload = ResponsesRequest(model="gpt-5.1", instructions="test", input="hello")

    response = await proxy_api._stream_responses(
        request,
        payload,
        context=cast(proxy_api.ProxyContext, context),
        api_key=None,
    )

    assert isinstance(response, StreamingResponse)
    assert response.status_code == 200
    assert response.headers["X-RateLimit-Limit"] == "1"
    chunks = [chunk async for chunk in response.body_iterator]
    body = "".join(chunk.decode() if isinstance(chunk, bytes) else str(chunk) for chunk in chunks)
    assert "response.failed" in body
    assert "rate_limit_exceeded" in body


def test_has_native_codex_transport_headers_requires_allowlisted_originator():
    assert proxy_module._has_native_codex_transport_headers({"originator": "codex_cli_rs"}) is True
    assert proxy_module._has_native_codex_transport_headers({"originator": "codex_exec"}) is True
    assert proxy_module._has_native_codex_transport_headers({"originator": "codex_vscode"}) is True
    assert proxy_module._has_native_codex_transport_headers({"originator": "codex_atlas"}) is True
    assert proxy_module._has_native_codex_transport_headers({"originator": "Codex Desktop"}) is True
    assert proxy_module._has_native_codex_transport_headers({"originator": "codex_chatgpt_desktop"}) is True
    assert proxy_module._has_native_codex_transport_headers({"originator": "Codex Chat"}) is False
    assert proxy_module._has_native_codex_transport_headers({"originator": "Codex QA"}) is False
    assert proxy_module._has_native_codex_transport_headers({"originator": "other-client"}) is False


def test_resolve_stream_transport_does_not_force_websocket_for_custom_codex_originator(monkeypatch) -> None:
    monkeypatch.setattr(
        proxy_module,
        "get_model_registry",
        lambda: SimpleNamespace(prefers_websockets=lambda _model: False),
    )

    transport = proxy_module._resolve_stream_transport(
        settings=SimpleNamespace(max_sse_event_bytes=16 * 1024 * 1024),
        transport="auto",
        transport_override=None,
        model="gpt-5.1",
        headers={"originator": "Codex QA"},
    )

    assert transport == "http"


def test_resolve_stream_transport_prefers_http_for_image_generation_even_with_native_codex_headers(monkeypatch) -> None:
    monkeypatch.setattr(
        proxy_module,
        "get_model_registry",
        lambda: SimpleNamespace(prefers_websockets=lambda model: model == "gpt-5.4"),
    )

    transport = proxy_module._resolve_stream_transport(
        settings=SimpleNamespace(max_sse_event_bytes=16 * 1024 * 1024),
        transport="auto",
        transport_override=None,
        model="gpt-5.4",
        headers={"originator": "codex_chatgpt_desktop"},
        has_image_generation_tool=True,
    )

    assert transport == "http"


def test_resolve_stream_transport_keeps_explicit_websocket_override_for_image_generation(monkeypatch) -> None:
    monkeypatch.setattr(
        proxy_module,
        "get_model_registry",
        lambda: SimpleNamespace(prefers_websockets=lambda _model: False),
    )

    transport = proxy_module._resolve_stream_transport(
        settings=SimpleNamespace(max_sse_event_bytes=16 * 1024 * 1024),
        transport="auto",
        transport_override="websocket",
        model="gpt-5.4",
        headers={},
        has_image_generation_tool=True,
    )

    assert transport == "websocket"


def test_resolve_stream_transport_uses_http_for_large_auto_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        proxy_module,
        "get_model_registry",
        lambda: SimpleNamespace(prefers_websockets=lambda model: model == "gpt-5.4"),
    )

    settings = SimpleNamespace(max_sse_event_bytes=16 * 1024 * 1024)
    transport = proxy_module._resolve_stream_transport(
        settings=settings,
        transport="auto",
        transport_override=None,
        model="gpt-5.4",
        headers={},
        payload_size_estimate_bytes=proxy_module._ws_transport_payload_budget_bytes(settings) + 1,
    )

    assert transport == "http"


def test_resolve_stream_transport_keeps_websocket_for_small_or_unknown_auto_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        proxy_module,
        "get_model_registry",
        lambda: SimpleNamespace(prefers_websockets=lambda model: model == "gpt-5.4"),
    )

    settings = SimpleNamespace(max_sse_event_bytes=16 * 1024 * 1024)

    assert (
        proxy_module._resolve_stream_transport(
            settings=settings,
            transport="auto",
            transport_override=None,
            model="gpt-5.4",
            headers={},
            payload_size_estimate_bytes=None,
        )
        == "websocket"
    )
    assert (
        proxy_module._resolve_stream_transport(
            settings=settings,
            transport="auto",
            transport_override=None,
            model="gpt-5.4",
            headers={},
            payload_size_estimate_bytes=proxy_module._ws_transport_payload_budget_bytes(settings),
        )
        == "websocket"
    )


def test_resolve_stream_transport_keeps_explicit_websocket_for_large_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        proxy_module,
        "get_model_registry",
        lambda: SimpleNamespace(prefers_websockets=lambda _model: False),
    )

    settings = SimpleNamespace(max_sse_event_bytes=16 * 1024 * 1024)
    transport = proxy_module._resolve_stream_transport(
        settings=settings,
        transport="websocket",
        transport_override=None,
        model="gpt-5.4",
        headers={},
        payload_size_estimate_bytes=proxy_module._ws_transport_payload_budget_bytes(settings) + 1,
    )

    assert transport == "websocket"


def test_resolve_stream_transport_keeps_explicit_http_for_large_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        proxy_module,
        "get_model_registry",
        lambda: SimpleNamespace(prefers_websockets=lambda model: model == "gpt-5.4"),
    )

    settings = SimpleNamespace(max_sse_event_bytes=16 * 1024 * 1024)
    transport = proxy_module._resolve_stream_transport(
        settings=settings,
        transport="http",
        transport_override=None,
        model="gpt-5.4",
        headers={"originator": "codex_chatgpt_desktop"},
        payload_size_estimate_bytes=proxy_module._ws_transport_payload_budget_bytes(settings) + 1,
    )

    assert transport == "http"


def test_ws_transport_payload_budget_uses_settings_limit() -> None:
    assert (
        proxy_module._ws_transport_payload_budget_bytes(SimpleNamespace(max_sse_event_bytes=16 * 1024 * 1024))
        == 14 * 1024 * 1024
    )
    assert proxy_module._ws_transport_payload_budget_bytes(SimpleNamespace(max_sse_event_bytes=2 * 1024 * 1024)) == (
        1 * 1024 * 1024
    )


@pytest.mark.asyncio
async def test_stream_http_bridge_or_retry_bypasses_bridge_for_large_payload(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    settings.max_sse_event_bytes = 16 * 1024 * 1024
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        proxy_service,
        "_http_bridge_runtime_config",
        lambda _dashboard_settings, _app_settings: proxy_service._HTTPBridgeRuntimeConfig(
            enabled=True,
            idle_ttl_seconds=30.0,
            codex_idle_ttl_seconds=30.0,
            max_sessions=8,
            queue_limit=16,
            prompt_cache_idle_ttl_seconds=30.0,
            gateway_safe_mode=False,
        ),
    )

    oversized_payload = MagicMock()
    oversized_payload.to_payload.return_value = {
        "model": "gpt-5.4",
        "input": "x" * (proxy_module._ws_transport_payload_budget_bytes(settings) + 1024),
    }
    resolve_file_account = AsyncMock(return_value="acc_pinned")
    monkeypatch.setattr(service, "_resolve_file_account_for_responses", resolve_file_account)

    calls: list[tuple[str, object, str | None]] = []

    async def fake_stream_with_retry(
        payload,
        headers,
        *,
        rewritten_file_account_id: str | None = None,
        **kwargs,
    ):
        del headers, kwargs
        calls.append(("retry", payload, rewritten_file_account_id))
        yield "data: retry\n\n"

    async def fake_stream_via_http_bridge(
        payload,
        headers,
        *,
        rewritten_file_account_id: str | None = None,
        **kwargs,
    ):
        del payload, headers, rewritten_file_account_id, kwargs
        calls.append(("bridge", None, None))
        yield "data: bridge\n\n"

    monkeypatch.setattr(service, "_stream_with_retry", fake_stream_with_retry)
    monkeypatch.setattr(service, "_stream_via_http_bridge", fake_stream_via_http_bridge)

    output = [
        line
        async for line in service._stream_http_bridge_or_retry(
            payload=oversized_payload,
            headers={},
            codex_session_affinity=False,
            propagate_http_errors=False,
            openai_cache_affinity=False,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
        )
    ]

    assert output == ["data: retry\n\n"]
    resolve_file_account.assert_awaited_once_with(oversized_payload, {})
    assert calls == [("retry", oversized_payload, "acc_pinned")]


def test_response_create_client_metadata_preserves_existing_json_values_and_turn_metadata():
    payload = {
        "client_metadata": {
            "bool_flag": True,
            "count": 2,
            "nested": {"enabled": False},
            "x-codex-turn-metadata": '{"turn_id":"payload-turn"}',
        }
    }

    metadata = proxy_service._response_create_client_metadata(
        payload,
        headers={"x-codex-turn-metadata": '{"turn_id":"header-turn"}'},
    )

    assert metadata == {
        "bool_flag": True,
        "count": 2,
        "nested": {"enabled": False},
        "x-codex-turn-metadata": '{"turn_id":"payload-turn"}',
    }


def test_response_create_client_metadata_reads_turn_metadata_case_insensitively():
    metadata = proxy_service._response_create_client_metadata(
        {},
        headers={"X-Codex-Turn-Metadata": '{"turn_id":"header-turn"}'},
    )

    assert metadata == {"x-codex-turn-metadata": '{"turn_id":"header-turn"}'}


def test_has_native_codex_transport_headers_does_not_treat_session_id_as_websocket_signal():
    assert proxy_module._has_native_codex_transport_headers({"session_id": "sid_123"}) is False


def test_has_native_codex_transport_headers_still_accepts_explicit_native_stream_headers_without_originator():
    assert proxy_module._has_native_codex_transport_headers({"x-codex-turn-metadata": "1"}) is True
    assert proxy_module._has_native_codex_transport_headers({"x-codex-beta-features": "repl"}) is True


def test_infer_websocket_handshake_error_code_detects_account_deactivated_message():
    code = proxy_module._infer_websocket_handshake_error_code(
        401,
        "Your OpenAI account has been deactivated, please check your email for more information.",
    )

    assert code == "account_deactivated"


def test_infer_websocket_handshake_error_code_keeps_generic_401_when_no_deactivation_hint():
    code = proxy_module._infer_websocket_handshake_error_code(
        401,
        "Unauthorized",
    )

    assert code == "invalid_api_key"


def test_parse_sse_event_reads_json_payload():
    payload = {"type": "response.completed", "response": {"id": "resp_1"}}
    line = f"data: {json.dumps(payload)}\n"
    event = parse_sse_event(line)
    assert event is not None
    assert event.type == "response.completed"
    assert event.response
    assert event.response.id == "resp_1"


def test_parse_sse_event_reads_multiline_payload():
    payload = {
        "type": "response.failed",
        "response": {"id": "resp_1", "status": "failed", "error": {"code": "upstream_error"}},
    }
    line = f"event: response.failed\ndata: {json.dumps(payload)}\n\n"
    event = parse_sse_event(line)
    assert event is not None
    assert event.type == "response.failed"
    assert event.response
    assert event.response.id == "resp_1"


def test_parse_sse_event_ignores_non_data_lines():
    assert parse_sse_event("event: ping\n") is None


def test_parse_sse_event_concats_multiple_data_lines():
    payload = {"type": "response.completed", "response": {"id": "resp_1"}}
    raw = json.dumps(payload)
    first, second = raw[: len(raw) // 2], raw[len(raw) // 2 :]
    line = f"data: {first}\ndata: {second}\n\n"

    event = parse_sse_event(line)

    assert event is not None
    assert event.type == "response.completed"


def test_normalize_sse_event_block_rewrites_response_text_alias():
    block = 'data: {"type":"response.text.delta","delta":"hi"}\n\n'

    normalized = proxy_module._normalize_sse_event_block(block)

    assert '"type":"response.output_text.delta"' in normalized
    assert normalized.endswith("\n\n")


def test_find_sse_separator_prefers_earliest_separator():
    buffer = b"event: one\n\ndata: two\r\n\r\n"

    result = proxy_module._find_sse_separator(buffer)

    assert result == (10, 2)


def test_pop_sse_event_returns_first_event_and_mutates_buffer():
    buffer = bytearray(b"data: one\n\ndata: two\n\n")

    event = proxy_module._pop_sse_event(buffer)

    assert event == b"data: one\n\n"
    assert bytes(buffer) == b"data: two\n\n"


class _DummyChunkIterator:
    def __init__(self, chunks: Sequence[bytes]) -> None:
        self._chunks = iter(chunks)

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._chunks)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _DummyContent(proxy_module.SSEContentProtocol):
    def __init__(self, chunks: Sequence[bytes]) -> None:
        self._chunks = list(chunks)

    def iter_chunked(self, size: int) -> _DummyChunkIterator:
        del size
        return _DummyChunkIterator(self._chunks)


class _DummyResponse(proxy_module.SSEResponseProtocol):
    content: proxy_module.SSEContentProtocol

    def __init__(self, chunks: Sequence[bytes]) -> None:
        self.content = _DummyContent(chunks)


class _TranscribeResponse:
    def __init__(self, payload: dict[str, object], *, json_error: Exception | None = None) -> None:
        self.status = 200
        self.reason = "OK"
        self._payload = payload
        self._json_error = json_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, *, content_type=None):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class _TranscribeSession:
    def __init__(self, response: _TranscribeResponse) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def post(
        self,
        url: str,
        *,
        data=None,
        headers: dict[str, str] | None = None,
        timeout=None,
    ):
        self.calls.append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        return self._response


class _TimeoutTranscribeSession:
    def post(
        self,
        url: str,
        *,
        data=None,
        headers: dict[str, str] | None = None,
        timeout=None,
    ):
        raise asyncio.TimeoutError


class _SettingsCache:
    def __init__(self, settings: object) -> None:
        self._settings = settings

    async def get(self) -> object:
        return self._settings


class _RequestLogsRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.response_owner_by_id: dict[tuple[str, str | None, str | None], str] = {}
        self.latest_response_by_session: dict[tuple[str, str | None], str] = {}
        self.lookup_calls: list[tuple[str, str | None, str | None]] = []
        self.session_lookup_calls: list[tuple[str, str | None]] = []
        self.lookup_error: Exception | None = None

    async def add_log(self, **kwargs: object) -> None:
        self.calls.append(dict(kwargs))

    async def find_latest_account_id_for_response_id(
        self,
        *,
        response_id: str,
        api_key_id: str | None,
        session_id: str | None = None,
    ) -> str | None:
        key = (response_id, api_key_id, session_id)
        self.lookup_calls.append(key)
        if self.lookup_error is not None:
            raise self.lookup_error
        owner = self.response_owner_by_id.get(key)
        if owner is not None:
            return owner
        if session_id is not None:
            return self.response_owner_by_id.get((response_id, api_key_id, None))
        return None

    async def find_latest_response_id_for_session_id(
        self,
        *,
        session_id: str,
        api_key_id: str | None,
    ) -> str | None:
        key = (session_id, api_key_id)
        self.session_lookup_calls.append(key)
        response_id = self.latest_response_by_session.get(key)
        if response_id is not None:
            return response_id
        if api_key_id is not None:
            return self.latest_response_by_session.get((session_id, None))
        return None


class _RepoContext:
    def __init__(self, request_logs: _RequestLogsRecorder) -> None:
        self._repos = ProxyRepositories(
            accounts=cast(AccountsRepository, AsyncMock()),
            usage=cast(UsageRepository, AsyncMock()),
            request_logs=cast(RequestLogsRepository, request_logs),
            sticky_sessions=cast(StickySessionsRepository, AsyncMock()),
            api_keys=cast(ApiKeysRepository, AsyncMock()),
            additional_usage=cast(AdditionalUsageRepository, AsyncMock()),
        )

    async def __aenter__(self) -> ProxyRepositories:
        return self._repos

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


def _repo_factory(request_logs: _RequestLogsRecorder) -> proxy_service.ProxyRepoFactory:
    def factory() -> _RepoContext:
        return _RepoContext(request_logs)

    return factory


def _make_proxy_settings(*, log_proxy_service_tier_trace: bool) -> SimpleNamespace:
    return SimpleNamespace(
        prefer_earlier_reset_accounts=False,
        sticky_threads_enabled=False,
        sticky_reallocation_budget_threshold_pct=95.0,
        upstream_stream_transport="default",
        openai_cache_affinity_max_age_seconds=300,
        openai_prompt_cache_key_derivation_enabled=True,
        routing_strategy="usage_weighted",
        proxy_request_budget_seconds=75.0,
        compact_request_budget_seconds=75.0,
        transcription_request_budget_seconds=120.0,
        upstream_compact_timeout_seconds=None,
        http_responses_session_bridge_gateway_safe_mode=False,
        log_proxy_request_payload=False,
        log_proxy_request_shape=False,
        log_proxy_request_shape_raw_cache_key=False,
        log_proxy_service_tier_trace=log_proxy_service_tier_trace,
        proxy_token_refresh_limit=32,
        proxy_upstream_websocket_connect_limit=64,
        proxy_response_create_limit=64,
        proxy_compact_response_create_limit=16,
        proxy_admission_wait_timeout_seconds=10.0,
        max_sse_event_bytes=16 * 1024 * 1024,
        http_responses_session_bridge_instance_id="test-instance",
        http_responses_session_bridge_instance_ring=[],
    )


@pytest.fixture(autouse=True)
def _install_default_proxy_runtime_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)


def _make_account(account_id: str) -> Account:
    encryptor = TokenEncryptor()
    now = utcnow()
    return Account(
        id=account_id,
        chatgpt_account_id=account_id,
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-token"),
        refresh_token_encrypted=encryptor.encrypt("refresh-token"),
        id_token_encrypted=encryptor.encrypt("id-token"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


class _JsonCompactResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.status = 200
        self.reason = "OK"
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, *, content_type=None):
        return self._payload


class _CompactSession:
    class _CompactResponseLike(Protocol):
        status: int

        async def __aenter__(self) -> Self: ...

        async def __aexit__(self, exc_type: object | None, exc: BaseException | None, tb: object | None) -> bool: ...

        async def json(self, *, content_type: str | None = None) -> dict[str, object]: ...

    def __init__(self, response: _CompactResponseLike) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def post(
        self,
        url: str,
        *,
        json=None,
        headers: dict[str, str] | None = None,
        timeout=None,
    ):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return self._response


class _SsePostResponse:
    def __init__(self, chunks: list[bytes]) -> None:
        self.status = 200
        self.content = _DummyContent(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _SseSession:
    def __init__(self, response: _SsePostResponse) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def post(
        self,
        url: str,
        *,
        json=None,
        headers: dict[str, str] | None = None,
        timeout=None,
    ):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return self._response


class _TimeoutSseSession:
    def post(
        self,
        url: str,
        *,
        json=None,
        headers: dict[str, str] | None = None,
        timeout=None,
    ):
        raise asyncio.TimeoutError


class _TimeoutCompactSession:
    def post(
        self,
        url: str,
        *,
        json=None,
        headers: dict[str, str] | None = None,
        timeout=None,
    ):
        raise asyncio.TimeoutError


class _WsConnection:
    def __init__(self, messages: Sequence[object]) -> None:
        self._messages = list(messages)
        self.sent_json: list[dict[str, object]] = []
        self.closed = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: object | None, exc: BaseException | None, tb: object | None) -> bool:
        self.closed = True
        return False

    async def send_json(self, payload: dict[str, object]) -> None:
        self.sent_json.append(payload)

    async def receive(self):
        if self._messages:
            return self._messages.pop(0)
        return SimpleNamespace(type=proxy_module.aiohttp.WSMsgType.CLOSE, data=None, extra=None)

    async def close(self) -> None:
        self.closed = True


def _ws_text_message(payload: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        type=proxy_module.aiohttp.WSMsgType.TEXT,
        data=json.dumps(payload, separators=(",", ":")),
        extra=None,
    )


class _WsResponse:
    def __init__(self, messages: Sequence[object], *, status: int = 101) -> None:
        self._messages = messages
        self._index = 0
        self._response = SimpleNamespace(status=status)
        self.closed = False
        self.sent_json: list[dict[str, object]] = []
        self.sent: list[str] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: object | None, exc: BaseException | None, tb: object | None) -> bool:
        self.closed = True
        return False

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self):
        if self._index >= len(self._messages):
            raise StopAsyncIteration
        message = self._messages[self._index]
        self._index += 1
        return message

    async def send_json(self, payload: dict[str, object]) -> None:
        self.sent_json.append(payload)

    async def send_str(self, data: str) -> None:
        self.sent.append(data)
        self.sent_json.append(json.loads(data))

    async def receive(self):
        if self._index >= len(self._messages):
            return _WsMessage(proxy_module.aiohttp.WSMsgType.CLOSED)
        message = self._messages[self._index]
        self._index += 1
        return message

    async def close(self) -> None:
        self.closed = True

    def exception(self):
        return None


class _WsMessage:
    def __init__(self, msg_type, data=None) -> None:
        self.type = msg_type
        self.data = data


class _WsSession:
    def __init__(
        self,
        response: _WsResponse | _WsConnection,
        sse_response: _SsePostResponse | None = None,
    ) -> None:
        self._response = response
        self._sse_response = sse_response
        self.ws_calls: list[dict[str, object]] = []
        self.post_calls: list[dict[str, object]] = []

    def ws_connect(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout=None,
        receive_timeout=None,
        heartbeat=None,
        autoclose=True,
        autoping=True,
        max_msg_size=None,
    ):
        self.ws_calls.append(
            {
                "url": url,
                "headers": headers,
                "timeout": timeout,
                "receive_timeout": receive_timeout,
                "heartbeat": heartbeat,
                "autoclose": autoclose,
                "autoping": autoping,
                "max_msg_size": max_msg_size,
            }
        )
        return self._response

    def post(
        self,
        url: str,
        *,
        json=None,
        headers: dict[str, str] | None = None,
        timeout=None,
    ):
        self.post_calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self._sse_response is None:
            raise AssertionError("HTTP POST path should not be used in websocket mode")
        return self._sse_response


@pytest.mark.asyncio
async def test_iter_sse_events_handles_large_single_line_without_chunk_too_big():
    large_data = "A" * (200 * 1024)
    event = f'data: {{"type":"response.output_text.delta","delta":"{large_data}"}}\n\n'.encode("utf-8")
    response = _DummyResponse([event[:4096], event[4096:]])
    stream = proxy_module._iter_sse_events(cast(proxy_module.SSEResponse, response), 1.0, 512 * 1024)

    chunks = [chunk async for chunk in stream]

    assert len(chunks) == 1
    assert chunks[0].startswith("data: ")
    assert chunks[0].endswith("\n\n")


@pytest.mark.asyncio
async def test_iter_sse_events_raises_on_event_size_limit():
    large_data = b"A" * 1024
    response = _DummyResponse([b"data: ", large_data])

    with pytest.raises(proxy_module.StreamEventTooLargeError):
        async for _ in proxy_module._iter_sse_events(cast(proxy_module.SSEResponse, response), 1.0, 256):
            pass


@pytest.mark.asyncio
async def test_iter_sse_events_raises_idle_timeout(monkeypatch):
    response = _DummyResponse([b'data: {"type":"response.in_progress"}\n\n'])

    async def fake_wait(tasks, *args, **kwargs):
        task = next(iter(tasks))
        task.cancel()
        return set(), set(tasks)

    monkeypatch.setattr(proxy_module.asyncio, "wait", fake_wait)

    with pytest.raises(proxy_module.StreamIdleTimeoutError):
        async for _ in proxy_module._iter_sse_events(cast(proxy_module.SSEResponse, response), 1.0, 1024):
            pass


@pytest.mark.asyncio
async def test_iter_sse_events_propagates_upstream_timeout():
    class _TimeoutContent:
        async def iter_chunked(self, size: int):
            if size <= 0:
                yield b""
            raise asyncio.TimeoutError

    class _TimeoutResponse:
        def __init__(self) -> None:
            self.content = _TimeoutContent()

    with pytest.raises(asyncio.TimeoutError):
        async for _ in proxy_module._iter_sse_events(cast(proxy_module.SSEResponse, _TimeoutResponse()), 1.0, 1024):
            pass


@pytest.mark.asyncio
async def test_iter_sse_events_cancels_pending_chunk_read():
    class _BlockingContent:
        def __init__(self) -> None:
            self.cancelled = asyncio.Event()

        async def iter_chunked(self, size: int):
            try:
                await asyncio.Future()
                if size < 0:
                    yield b""
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

    class _BlockingResponse:
        def __init__(self) -> None:
            self.content = _BlockingContent()

    response = _BlockingResponse()

    async def consume() -> None:
        async for _ in proxy_module._iter_sse_events(cast(proxy_module.SSEResponse, response), 10.0, 1024):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert response.content.cancelled.is_set()


def test_log_proxy_request_payload(monkeypatch, caplog):
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    class Settings:
        log_proxy_request_payload = True
        log_proxy_request_shape = False
        log_proxy_request_shape_raw_cache_key = False

    monkeypatch.setattr(proxy_service, "get_settings", lambda: Settings())

    token = set_request_id("req_log_1")
    try:
        caplog.set_level(logging.WARNING)
        proxy_service._maybe_log_proxy_request_payload("stream", payload, {"X-Request-Id": "req_log_1"})
    finally:
        reset_request_id(token)

    assert "proxy_request_payload" in caplog.text
    assert '"model":"gpt-5.1"' in caplog.text


def test_log_proxy_request_shape_includes_affinity_metadata(monkeypatch, caplog):
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            "tools": [{"type": "function", "name": "b_tool"}, {"type": "function", "name": "a_tool"}],
        }
    )

    class Settings:
        log_proxy_request_payload = False
        log_proxy_request_shape = True
        log_proxy_request_shape_raw_cache_key = False
        log_proxy_service_tier_trace = False

    monkeypatch.setattr(proxy_service, "get_settings", lambda: Settings())

    token = set_request_id("req_shape_1")
    try:
        caplog.set_level(logging.WARNING)
        proxy_service._maybe_log_proxy_request_shape(
            "stream",
            payload,
            {"session_id": "sid_1"},
            sticky_kind="codex_session",
            sticky_key_source="session_header",
            prompt_cache_key_set=True,
        )
    finally:
        reset_request_id(token)

    assert "proxy_request_shape" in caplog.text
    assert "sticky_kind=codex_session" in caplog.text
    assert "sticky_key_source=session_header" in caplog.text
    assert "prompt_cache_key_set=True" in caplog.text
    assert "session_header_present=True" in caplog.text
    assert "tools_hash=sha256:" in caplog.text


def test_log_proxy_request_shape_hashes_prompt_cache_key_without_raw_value(monkeypatch, caplog):
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            "prompt_cache_key": "thread_secret_123",
        }
    )

    class Settings:
        log_proxy_request_payload = False
        log_proxy_request_shape = True
        log_proxy_request_shape_raw_cache_key = False
        log_proxy_service_tier_trace = False

    monkeypatch.setattr(proxy_service, "get_settings", lambda: Settings())

    token = set_request_id("req_shape_2")
    try:
        caplog.set_level(logging.WARNING)
        proxy_service._maybe_log_proxy_request_shape(
            "stream",
            payload,
            {},
            sticky_kind="prompt_cache",
            sticky_key_source="payload",
            prompt_cache_key_set=True,
        )
    finally:
        reset_request_id(token)

    assert "prompt_cache_key=sha256:" in caplog.text
    assert "thread_secret_123" not in caplog.text


def test_log_proxy_request_shape_reports_derived_key_after_affinity_resolution(monkeypatch, caplog):
    class Settings:
        log_proxy_request_payload = False
        log_proxy_request_shape = True
        log_proxy_request_shape_raw_cache_key = False
        log_proxy_service_tier_trace = False
        openai_prompt_cache_key_derivation_enabled = True

    monkeypatch.setattr(proxy_service, "get_settings", lambda: Settings())

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            "stream": True,
        }
    )
    proxy_service._sticky_key_for_responses_request(
        payload,
        headers={"session_id": "sid_1"},
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
    )

    token = set_request_id("req_shape_3")
    try:
        caplog.set_level(logging.WARNING)
        proxy_service._maybe_log_proxy_request_shape(
            "stream",
            payload,
            {"session_id": "sid_1"},
            sticky_kind="codex_session",
            sticky_key_source="session_header",
            prompt_cache_key_set=True,
        )
    finally:
        reset_request_id(token)

    assert "prompt_cache_key=sha256:" in caplog.text
    assert "prompt_cache_key_raw=None" in caplog.text


def test_log_proxy_service_tier_trace(monkeypatch, caplog):
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "secret instructions",
            "input": [{"role": "user", "content": "secret prompt"}],
            "service_tier": "priority",
        }
    )

    class Settings:
        log_proxy_request_payload = False
        log_proxy_request_shape = False
        log_proxy_request_shape_raw_cache_key = False
        log_proxy_service_tier_trace = True

    monkeypatch.setattr(proxy_service, "get_settings", lambda: Settings())

    token = set_request_id("req_tier_trace_1")
    try:
        caplog.set_level(logging.WARNING)
        proxy_service._maybe_log_proxy_service_tier_trace(
            "stream",
            requested_service_tier=payload.service_tier,
            actual_service_tier="default",
        )
    finally:
        reset_request_id(token)

    assert "proxy_service_tier_trace" in caplog.text
    assert "request_id=req_tier_trace_1" in caplog.text
    assert "kind=stream" in caplog.text
    assert "requested_service_tier=priority" in caplog.text
    assert "actual_service_tier=default" in caplog.text
    assert "secret instructions" not in caplog.text
    assert "secret prompt" not in caplog.text


def test_log_proxy_service_tier_trace_disabled(monkeypatch, caplog):
    class Settings:
        log_proxy_request_payload = False
        log_proxy_request_shape = False
        log_proxy_request_shape_raw_cache_key = False
        log_proxy_service_tier_trace = False

    monkeypatch.setattr(proxy_service, "get_settings", lambda: Settings())

    token = set_request_id("req_tier_trace_2")
    try:
        caplog.set_level(logging.WARNING)
        proxy_service._maybe_log_proxy_service_tier_trace(
            "compact",
            requested_service_tier="priority",
            actual_service_tier=None,
        )
    finally:
        reset_request_id(token)

    assert "proxy_service_tier_trace" not in caplog.text


def test_log_upstream_request_trace(monkeypatch, caplog):
    class Settings:
        log_upstream_request_summary = True
        log_upstream_request_payload = True

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())

    token = set_request_id("req_upstream_1")
    try:
        caplog.set_level(logging.INFO)
        headers = _build_upstream_headers({"session_id": "sid_1"}, "token", "acc_upstream_1")
        payload_json = '{"model":"gpt-5.4","input":"hi"}'
        proxy_module._maybe_log_upstream_request_start(
            kind="responses",
            url="https://chatgpt.com/backend-api/codex/responses",
            headers=headers,
            method="POST",
            payload_summary="model=gpt-5.4 stream=True input=str keys=['input','model','stream']",
            payload_json=payload_json,
        )
        proxy_module._maybe_log_upstream_request_complete(
            kind="responses",
            url="https://chatgpt.com/backend-api/codex/responses",
            headers=headers,
            method="POST",
            started_at=0.0,
            status_code=502,
            error_code="upstream_error",
            error_message="backend exploded",
        )
    finally:
        reset_request_id(token)

    assert "upstream_request_start request_id=req_upstream_1" in caplog.text
    assert "upstream_request_payload request_id=req_upstream_1" in caplog.text
    assert "upstream_request_complete request_id=req_upstream_1" in caplog.text
    assert "target=https://chatgpt.com/backend-api/codex/responses" in caplog.text
    assert "error_message=backend exploded" in caplog.text


@pytest.mark.asyncio
async def test_stream_responses_starts_upstream_timer_after_image_inlining(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 1.0
        stream_idle_timeout_seconds = 1.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = True
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 15.0
        upstream_stream_transport = "http"

    inline_ran = False
    recorded: dict[str, float | None] = {}

    async def fake_inline(payload_dict, session, connect_timeout):
        nonlocal inline_ran
        inline_ran = True
        return payload_dict

    monotonic_values = iter([100.0, 104.0, 104.0, 104.0])

    def fake_monotonic():
        return next(monotonic_values, 104.0)

    def fake_complete(**kwargs):
        recorded["started_at"] = kwargs["started_at"]

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_inline_input_image_urls", fake_inline)
    monkeypatch.setattr(proxy_module.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", fake_complete)

    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )
    session = _SseSession(_SsePostResponse([b'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n']))

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    timeout = session.calls[0]["timeout"]
    assert isinstance(timeout, proxy_module.aiohttp.ClientTimeout)
    assert timeout.total == pytest.approx(11.0)
    assert events == ['data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n']
    assert recorded["started_at"] == 104.0


@pytest.mark.asyncio
async def test_stream_responses_honors_timeout_overrides(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        upstream_stream_transport = "http"

    seen: dict[str, object] = {}

    async def fake_iter(resp, idle_timeout_seconds, max_event_bytes):
        seen["idle_timeout_seconds"] = idle_timeout_seconds
        seen["max_event_bytes"] = max_event_bytes
        yield 'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_iter_sse_events", fake_iter)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )
    session = _SseSession(_SsePostResponse([b"unused"]))

    token = set_request_id("req_timeout_override")
    try:
        with proxy_module.override_stream_timeouts(
            connect_timeout_seconds=2.5,
            idle_timeout_seconds=3.5,
            total_timeout_seconds=4.5,
        ):
            events = [
                event
                async for event in proxy_module.stream_responses(
                    payload,
                    headers={},
                    access_token="token",
                    account_id="acc_1",
                    session=cast(proxy_module.aiohttp.ClientSession, session),
                )
            ]
    finally:
        reset_request_id(token)

    assert events == ['data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n']
    timeout = session.calls[0]["timeout"]
    assert isinstance(timeout, proxy_module.aiohttp.ClientTimeout)
    assert timeout.total == pytest.approx(4.5, abs=0.01)
    assert timeout.sock_connect == pytest.approx(2.5)
    assert seen["idle_timeout_seconds"] == pytest.approx(3.5)


@pytest.mark.asyncio
async def test_stream_responses_maps_total_timeout_to_request_timeout(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 5.0
        upstream_stream_transport = "http"

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, _TimeoutSseSession()),
        )
    ]

    event = json.loads(events[0].split("data: ", 1)[1])
    assert event["response"]["error"]["code"] == "upstream_request_timeout"


@pytest.mark.asyncio
async def test_stream_responses_maps_connect_timeout_to_upstream_unavailable(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 5.0
        upstream_stream_transport = "http"

    class _ConnectTimeoutSseSession:
        def post(
            self,
            url: str,
            *,
            json=None,
            headers: dict[str, str] | None = None,
            timeout=None,
        ):
            raise proxy_module.aiohttp.ConnectionTimeoutError("connect timed out")

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, _ConnectTimeoutSseSession()),
        )
    ]

    event = json.loads(events[0].split("data: ", 1)[1])
    assert event["response"]["error"]["code"] == "upstream_unavailable"


@pytest.mark.asyncio
async def test_stream_responses_uses_native_websocket_upstream_for_codex_headers(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024 * 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 15.0
        upstream_stream_transport = "auto"

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [{"role": "user", "content": "hi"}],
            "stream": True,
            "service_tier": "priority",
        }
    )
    websocket = _WsConnection(
        [
            _ws_text_message(
                {
                    "type": "response.created",
                    "response": {"id": "resp_ws_1", "status": "in_progress", "service_tier": "auto"},
                }
            ),
            _ws_text_message(
                {
                    "type": "response.completed",
                    "response": {"id": "resp_ws_1", "status": "completed", "service_tier": "default"},
                }
            ),
        ]
    )
    session = _WsSession(websocket)

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={
                "originator": "codex_cli_rs",
                "session_id": "sid-native",
                "x-codex-turn-metadata": '{"turn_id":"turn_123","sandbox":"none"}',
                "x-codex-beta-features": "js_repl,multi_agent",
                "user-agent": "codex_cli_rs/0.114.0",
            },
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    assert len(session.ws_calls) == 1
    assert session.post_calls == []
    assert session.ws_calls[0]["url"] == "wss://chatgpt.com/backend-api/codex/responses"
    headers = cast(dict[str, str], session.ws_calls[0]["headers"])
    assert headers is not None
    assert headers["Authorization"] == "Bearer token"
    assert headers["chatgpt-account-id"] == "acc_1"
    assert headers["originator"] == "codex_cli_rs"
    assert "Content-Type" not in headers
    assert "Accept" not in headers
    expected_request_payload = {
        "type": "response.create",
        **{k: v for k, v in payload.to_payload().items() if k != "stream"},
    }
    assert websocket.sent_json == [expected_request_payload]
    assert len(events) == 2
    created = parse_sse_event(events[0])
    completed = parse_sse_event(events[1])
    created_payload = parse_sse_data_json(events[0])
    completed_payload = parse_sse_data_json(events[1])
    assert created is not None
    assert completed is not None
    assert created.response is not None
    assert completed.response is not None
    created_response = cast(dict[str, object], cast(dict[str, object], created_payload)["response"])
    completed_response = cast(dict[str, object], cast(dict[str, object], completed_payload)["response"])
    assert created_response["service_tier"] == "auto"
    assert completed_response["service_tier"] == "default"


@pytest.mark.asyncio
async def test_stream_responses_falls_back_to_http_post_without_native_codex_headers(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 15.0
        upstream_stream_transport = "http"

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )
    session = _WsSession(
        _WsConnection([]),
        sse_response=_SsePostResponse([b'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n']),
    )

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    assert session.ws_calls == []
    assert len(session.post_calls) == 1
    assert events == ['data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n']


@pytest.mark.asyncio
async def test_stream_responses_uses_websocket_transport(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "websocket"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    messages = [
        SimpleNamespace(
            type=proxy_module.aiohttp.WSMsgType.TEXT,
            data='{"type":"response.created","response":{"id":"resp_ws","service_tier":"auto"}}',
        ),
        SimpleNamespace(
            type=proxy_module.aiohttp.WSMsgType.TEXT,
            data='{"type":"response.completed","response":{"id":"resp_ws","service_tier":"default"}}',
        ),
    ]
    websocket = _WsResponse(messages)
    session = _WsSession(websocket)
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={"originator": "codex_cli_rs", "session_id": "sid_ws"},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    assert session.ws_calls[0]["url"] == "wss://chatgpt.com/backend-api/codex/responses"
    request_payload = websocket.sent_json[0]
    expected_request_payload = {
        "type": "response.create",
        **{k: v for k, v in payload.to_payload().items() if k != "stream"},
    }
    assert request_payload == expected_request_payload
    expected_created = (
        "event: response.created\ndata: "
        '{"type":"response.created","response":{"id":"resp_ws","service_tier":"auto"}}\n\n'
    )
    expected_completed = (
        "event: response.completed\ndata: "
        '{"type":"response.completed","response":{"id":"resp_ws","service_tier":"default"}}\n\n'
    )
    assert events == [
        expected_created,
        expected_completed,
    ]


@pytest.mark.asyncio
async def test_stream_responses_websocket_rejects_oversized_response_create_before_connect(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "websocket"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_UPSTREAM_RESPONSE_CREATE_WARN_BYTES", 64, raising=False)
    monkeypatch.setattr(proxy_module, "_UPSTREAM_RESPONSE_CREATE_MAX_BYTES", 128, raising=False)

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "x" * 256}]}],
        }
    )
    session = _WsSession(_WsResponse([]))

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        _ = [
            event
            async for event in proxy_module.stream_responses(
                payload,
                headers={},
                access_token="token",
                account_id="acc_1",
                session=cast(proxy_module.aiohttp.ClientSession, session),
                raise_for_status=True,
            )
        ]

    assert exc_info.value.status_code == 413
    assert exc_info.value.payload["error"]["code"] == "payload_too_large"
    assert session.ws_calls == []


@pytest.mark.asyncio
async def test_stream_responses_websocket_slims_historical_inline_artifacts_and_succeeds(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "websocket"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_UPSTREAM_RESPONSE_CREATE_WARN_BYTES", 64, raising=False)
    monkeypatch.setattr(proxy_module, "_UPSTREAM_RESPONSE_CREATE_MAX_BYTES", 640, raising=False)

    messages = [
        SimpleNamespace(
            type=proxy_module.aiohttp.WSMsgType.TEXT,
            data='{"type":"response.created","response":{"id":"resp_ws_slim","service_tier":"auto"}}',
        ),
        SimpleNamespace(
            type=proxy_module.aiohttp.WSMsgType.TEXT,
            data='{"type":"response.completed","response":{"id":"resp_ws_slim","service_tier":"default"}}',
        ),
    ]
    websocket = _WsResponse(messages)
    session = _WsSession(websocket)
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": [
                {"role": "user", "content": [{"type": "input_text", "text": "old turn"}]},
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "data:image/png;base64," + ("A" * 1200),
                },
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": "data:image/png;base64," + ("B" * 1200),
                        }
                    ],
                },
                {"role": "user", "content": [{"type": "input_text", "text": "latest turn"}]},
            ],
        }
    )

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    assert len(events) == 2
    assert len(session.ws_calls) == 1
    request_payload = websocket.sent_json[0]
    request_input = cast(list[dict[str, object]], request_payload["input"])
    assert request_input[1]["output"] == proxy_service._RESPONSE_CREATE_TOOL_OUTPUT_OMISSION_NOTICE.format(
        bytes=len(("data:image/png;base64," + ("A" * 1200)).encode("utf-8"))
    )
    assistant_item = request_input[2]
    assert assistant_item["content"] == [
        {"type": "input_text", "text": proxy_service._RESPONSE_CREATE_IMAGE_OMISSION_NOTICE}
    ]
    assert request_input[-1] == {"role": "user", "content": [{"type": "input_text", "text": "latest turn"}]}


@pytest.mark.asyncio
async def test_stream_responses_websocket_forces_response_create_event_type(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "websocket"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": "hi"}],
            "type": "response.cancel",
            "custom_flag": "x",
        }
    )
    websocket = _WsResponse(
        [
            _WsMessage(
                proxy_module.aiohttp.WSMsgType.TEXT,
                json.dumps({"type": "response.completed", "response": {"id": "resp_ws"}}),
            )
        ]
    )
    session = _WsSession(websocket)

    _ = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    request_payload = websocket.sent_json[0]
    assert payload.to_payload()["type"] == "response.cancel"
    assert request_payload["type"] == "response.create"
    assert request_payload["custom_flag"] == "x"


@pytest.mark.asyncio
async def test_stream_responses_websocket_omits_http_only_transport_fields(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "websocket"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": "hi"}],
            "stream": True,
            "background": True,
            "custom_flag": "x",
        }
    )
    websocket = _WsResponse(
        [
            _WsMessage(
                proxy_module.aiohttp.WSMsgType.TEXT,
                json.dumps({"type": "response.completed", "response": {"id": "resp_ws"}}),
            )
        ]
    )
    session = _WsSession(websocket)

    _ = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    request_payload = websocket.sent_json[0]
    assert request_payload["type"] == "response.create"
    assert request_payload["custom_flag"] == "x"
    assert "stream" not in request_payload
    assert "background" not in request_payload


@pytest.mark.asyncio
async def test_stream_responses_via_websocket_counts_connect_and_send_against_total_timeout(monkeypatch):
    recorded: dict[str, float | None] = {}
    websocket = _WsResponse([])
    monotonic_values = iter([100.0, 100.0, 104.75, 104.75, 104.75, 104.75])

    def fake_monotonic() -> float:
        return next(monotonic_values, 104.75)

    async def fake_open_upstream_websocket(
        *,
        session,
        url: str,
        headers,
        connect_timeout_seconds: float,
        max_msg_size: int,
        account_id: str | None = None,
        hold_half_open_probe: bool = False,
    ):
        del session, url, headers, max_msg_size, account_id, hold_half_open_probe
        recorded["connect_timeout_seconds"] = connect_timeout_seconds
        return websocket, websocket

    async def fake_stream_websocket_events(
        websocket_obj,
        *,
        idle_timeout_seconds: float,
        total_timeout_seconds: float | None,
        max_event_bytes: int,
    ):
        recorded["total_timeout_seconds"] = total_timeout_seconds
        if False:
            yield ""

    monkeypatch.setattr(proxy_module, "_open_upstream_websocket", fake_open_upstream_websocket)
    monkeypatch.setattr(proxy_module, "_stream_websocket_events", fake_stream_websocket_events)
    monkeypatch.setattr(proxy_module.time, "monotonic", fake_monotonic)

    events = [
        event
        async for event in proxy_module._stream_responses_via_websocket(
            payload_dict={"model": "gpt-5.1", "type": "response.cancel"},
            url="https://chatgpt.com/backend-api/codex/responses",
            headers={"originator": "codex_cli_rs"},
            client_session=cast(proxy_module.aiohttp.ClientSession, SimpleNamespace()),
            effective_total_timeout=5.0,
            effective_connect_timeout=8.0,
            effective_idle_timeout=45.0,
            max_event_bytes=1024,
            raise_for_status=True,
        )
    ]

    assert events == []
    assert recorded["connect_timeout_seconds"] == pytest.approx(5.0)
    assert recorded["total_timeout_seconds"] == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_open_upstream_websocket_preserves_error_body_on_handshake_failure():
    error_body = json.dumps(
        {"error": {"message": "quota exhausted", "type": "server_error", "code": "insufficient_quota"}}
    )

    class _HandshakeFailureResponse:
        def __init__(self) -> None:
            self.status = 403
            self.headers = {}
            self.request_info = SimpleNamespace(real_url="wss://chatgpt.com/backend-api/codex/responses")
            self.history = ()
            self.connection = None
            self.closed = False

        async def text(self) -> str:
            return error_body

        def close(self) -> None:
            self.closed = True

    class _HandshakeFailureSession:
        def __init__(self) -> None:
            self._loop = asyncio.get_running_loop()
            self._ws_response_class = proxy_module.aiohttp.ClientWebSocketResponse

        async def request(self, method, url, **kwargs):
            del method, url, kwargs
            return _HandshakeFailureResponse()

    with pytest.raises(proxy_module.aiohttp.WSServerHandshakeError) as exc_info:
        await proxy_module._open_upstream_websocket(
            session=cast(proxy_module.aiohttp.ClientSession, _HandshakeFailureSession()),
            url="wss://chatgpt.com/backend-api/codex/responses",
            headers={"Authorization": "Bearer token"},
            connect_timeout_seconds=8.0,
            max_msg_size=1024,
        )

    assert "insufficient_quota" in exc_info.value.message


@pytest.mark.asyncio
async def test_open_upstream_websocket_records_circuit_breaker_failure_on_5xx_handshake(monkeypatch):
    class _CircuitBreakerStub:
        def __init__(self) -> None:
            self.state = CircuitState.CLOSED
            self.failures: list[Exception] = []
            self.successes = 0

        async def pre_call_check(self) -> bool:
            return False

        async def release_half_open_probe(self) -> None:
            pass

        async def _record_failure(self, exc: Exception) -> None:
            self.failures.append(exc)

        async def _record_success(self) -> None:
            self.successes += 1

    class _HandshakeFailureResponse:
        def __init__(self) -> None:
            self.status = 503
            self.headers = {}
            self.request_info = SimpleNamespace(real_url="wss://chatgpt.com/backend-api/codex/responses")
            self.history = ()
            self.connection = None

        async def text(self) -> str:
            return "upstream unavailable"

        def close(self) -> None:
            return None

    class _HandshakeFailureSession:
        def __init__(self) -> None:
            self._loop = asyncio.get_running_loop()
            self._ws_response_class = proxy_module.aiohttp.ClientWebSocketResponse

        async def request(self, method, url, **kwargs):
            del method, url, kwargs
            return _HandshakeFailureResponse()

    cb = _CircuitBreakerStub()
    monkeypatch.setattr(proxy_module, "get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=True))
    monkeypatch.setattr(proxy_module, "get_circuit_breaker_for_account", lambda _aid, _settings: cb)

    with pytest.raises(proxy_module.aiohttp.WSServerHandshakeError):
        await proxy_module._open_upstream_websocket(
            session=cast(proxy_module.aiohttp.ClientSession, _HandshakeFailureSession()),
            url="wss://chatgpt.com/backend-api/codex/responses",
            headers={"Authorization": "Bearer token"},
            connect_timeout_seconds=8.0,
            max_msg_size=1024,
            account_id="acc_test",
        )

    assert cb.successes == 0
    assert len(cb.failures) == 1
    assert str(cb.failures[0]) == "WebSocket handshake failed: HTTP 503"


@pytest.mark.asyncio
async def test_open_upstream_websocket_records_circuit_breaker_success_after_valid_handshake(monkeypatch):
    class _CircuitBreakerStub:
        def __init__(self) -> None:
            self.state = CircuitState.CLOSED
            self.failures: list[Exception] = []
            self.successes = 0

        async def pre_call_check(self) -> bool:
            return False

        async def release_half_open_probe(self) -> None:
            pass

        async def _record_failure(self, exc: Exception) -> None:
            self.failures.append(exc)

        async def _record_success(self) -> None:
            self.successes += 1

    class _ProtocolStub:
        def __init__(self) -> None:
            self.read_timeout: float | None = 10.0

        def set_parser(self, parser, reader) -> None:
            del parser, reader

    class _ConnectionStub:
        def __init__(self) -> None:
            self.protocol = _ProtocolStub()
            self.transport = object()

    class _HandshakeSuccessResponse:
        def __init__(self, headers: dict[str, str]) -> None:
            self.status = 101
            self.headers = headers
            self.request_info = SimpleNamespace(real_url="wss://chatgpt.com/backend-api/codex/responses")
            self.history = ()
            self.connection = _ConnectionStub()

        async def text(self) -> str:
            return ""

        def close(self) -> None:
            return None

    class _HandshakeSuccessSession:
        def __init__(self) -> None:
            self._loop = asyncio.get_running_loop()

            def _build_ws(*args, **kwargs):
                del args, kwargs
                return SimpleNamespace(tag="ws")

            self._ws_response_class = _build_ws

        async def request(self, method, url, **kwargs):
            del method, url
            sec_key = kwargs["headers"][proxy_module.hdrs.SEC_WEBSOCKET_KEY]
            response_key = proxy_module.base64.b64encode(
                proxy_module.hashlib.sha1(sec_key.encode() + proxy_module.WS_KEY).digest()
            ).decode()
            return _HandshakeSuccessResponse(
                {
                    proxy_module.hdrs.UPGRADE: "websocket",
                    proxy_module.hdrs.CONNECTION: "upgrade",
                    proxy_module.hdrs.SEC_WEBSOCKET_ACCEPT: response_key,
                }
            )

    cb = _CircuitBreakerStub()
    monkeypatch.setattr(proxy_module, "get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=True))
    monkeypatch.setattr(proxy_module, "get_circuit_breaker_for_account", lambda _aid, _settings: cb)
    monkeypatch.setattr(proxy_module.aiohttp.client_ws, "WebSocketDataQueue", lambda *args, **kwargs: object())
    monkeypatch.setattr(proxy_module, "WebSocketReader", lambda *args, **kwargs: object())
    monkeypatch.setattr(proxy_module, "WebSocketWriter", lambda *args, **kwargs: object())

    websocket_cm, websocket = await proxy_module._open_upstream_websocket(
        session=cast(proxy_module.aiohttp.ClientSession, _HandshakeSuccessSession()),
        url="wss://chatgpt.com/backend-api/codex/responses",
        headers={"Authorization": "Bearer token"},
        connect_timeout_seconds=8.0,
        max_msg_size=1024,
        account_id="acc_test",
    )

    assert websocket_cm == websocket
    assert cb.failures == []
    assert cb.successes == 0


@pytest.mark.asyncio
async def test_open_upstream_websocket_holds_half_open_probe_until_lifecycle_finishes(monkeypatch):
    class _CircuitBreakerStub:
        def __init__(self) -> None:
            self.state = CircuitState.CLOSED
            self.failures: list[Exception] = []
            self.successes = 0
            self.release_calls = 0

        async def pre_call_check(self) -> bool:
            return True

        async def release_half_open_probe(self) -> None:
            self.release_calls += 1

        async def _record_failure(self, exc: Exception) -> None:
            self.failures.append(exc)

        async def _record_success(self) -> None:
            self.successes += 1

    class _ProtocolStub:
        def __init__(self) -> None:
            self.read_timeout: float | None = 10.0

        def set_parser(self, parser, reader) -> None:
            del parser, reader

    class _ConnectionStub:
        def __init__(self) -> None:
            self.protocol = _ProtocolStub()
            self.transport = object()

    class _HandshakeSuccessResponse:
        def __init__(self, headers: dict[str, str]) -> None:
            self.status = 101
            self.headers = headers
            self.request_info = SimpleNamespace(real_url="wss://chatgpt.com/backend-api/codex/responses")
            self.history = ()
            self.connection = _ConnectionStub()

        async def text(self) -> str:
            return ""

        def close(self) -> None:
            return None

    class _HandshakeSuccessSession:
        def __init__(self) -> None:
            self._loop = asyncio.get_running_loop()

            def _build_ws(*args, **kwargs):
                del args, kwargs
                return SimpleNamespace(tag="ws")

            self._ws_response_class = _build_ws

        async def request(self, method, url, **kwargs):
            del method, url
            sec_key = kwargs["headers"][proxy_module.hdrs.SEC_WEBSOCKET_KEY]
            response_key = proxy_module.base64.b64encode(
                proxy_module.hashlib.sha1(sec_key.encode() + proxy_module.WS_KEY).digest()
            ).decode()
            return _HandshakeSuccessResponse(
                {
                    proxy_module.hdrs.UPGRADE: "websocket",
                    proxy_module.hdrs.CONNECTION: "upgrade",
                    proxy_module.hdrs.SEC_WEBSOCKET_ACCEPT: response_key,
                }
            )

    cb = _CircuitBreakerStub()
    monkeypatch.setattr(proxy_module, "get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=True))
    monkeypatch.setattr(proxy_module, "get_circuit_breaker_for_account", lambda _aid, _settings: cb)
    monkeypatch.setattr(proxy_module.aiohttp.client_ws, "WebSocketDataQueue", lambda *args, **kwargs: object())
    monkeypatch.setattr(proxy_module, "WebSocketReader", lambda *args, **kwargs: object())
    monkeypatch.setattr(proxy_module, "WebSocketWriter", lambda *args, **kwargs: object())

    _, websocket = await proxy_module._open_upstream_websocket(
        session=cast(proxy_module.aiohttp.ClientSession, _HandshakeSuccessSession()),
        url="wss://chatgpt.com/backend-api/codex/responses",
        headers={"Authorization": "Bearer token"},
        connect_timeout_seconds=8.0,
        max_msg_size=1024,
        account_id="acc_test",
        hold_half_open_probe=True,
    )

    assert cb.release_calls == 0
    assert getattr(websocket, "_codex_lb_half_open_probe_held", False) is True


@pytest.mark.asyncio
async def test_stream_responses_websocket_records_circuit_breaker_success_after_terminal_event(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "websocket"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False
        circuit_breaker_enabled = True

    class _CircuitBreakerStub:
        def __init__(self) -> None:
            self.failures: list[Exception] = []
            self.successes = 0

        async def _record_failure(self, exc: Exception) -> None:
            self.failures.append(exc)

        async def _record_success(self) -> None:
            self.successes += 1

    websocket = _WsResponse(
        [
            _WsMessage(
                proxy_module.aiohttp.WSMsgType.TEXT,
                json.dumps({"type": "response.completed", "response": {"id": "resp_ws"}}),
            )
        ]
    )
    breaker = _CircuitBreakerStub()
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    async def fake_open_upstream_websocket(**kwargs):
        del kwargs
        return websocket, websocket

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "get_circuit_breaker_for_account", lambda _aid, _settings: breaker)
    monkeypatch.setattr(proxy_module, "_open_upstream_websocket", fake_open_upstream_websocket)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    _ = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, _WsSession(websocket)),
        )
    ]

    assert breaker.successes == 1
    assert breaker.failures == []


@pytest.mark.asyncio
async def test_stream_responses_websocket_records_circuit_breaker_failure_when_stream_closes_without_terminal(
    monkeypatch,
):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "websocket"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False
        circuit_breaker_enabled = True

    class _CircuitBreakerStub:
        def __init__(self) -> None:
            self.failures: list[Exception] = []
            self.successes = 0

        async def _record_failure(self, exc: Exception) -> None:
            self.failures.append(exc)

        async def _record_success(self) -> None:
            self.successes += 1

    websocket = _WsResponse([])
    breaker = _CircuitBreakerStub()
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    async def fake_open_upstream_websocket(**kwargs):
        del kwargs
        return websocket, websocket

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "get_circuit_breaker_for_account", lambda _aid, _settings: breaker)
    monkeypatch.setattr(proxy_module, "_open_upstream_websocket", fake_open_upstream_websocket)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, _WsSession(websocket)),
        )
    ]

    assert breaker.successes == 0
    assert len(breaker.failures) == 1
    assert any("stream_incomplete" in event for event in events)


@pytest.mark.asyncio
async def test_open_upstream_websocket_raises_when_circuit_breaker_is_open(monkeypatch):
    class _CircuitBreakerStub:
        def __init__(self) -> None:
            self.state = CircuitState.OPEN
            self.failures: list[Exception] = []
            self.successes = 0

        async def pre_call_check(self) -> bool:
            from app.core.resilience.circuit_breaker import CircuitBreakerOpenError

            raise CircuitBreakerOpenError("Circuit breaker is OPEN")

        async def release_half_open_probe(self) -> None:
            pass

        async def _record_failure(self, exc: Exception) -> None:
            self.failures.append(exc)

        async def _record_success(self) -> None:
            self.successes += 1

    called = False

    class _Session:
        def __init__(self) -> None:
            self._loop = asyncio.get_running_loop()
            self._ws_response_class = proxy_module.aiohttp.ClientWebSocketResponse

        async def request(self, method, url, **kwargs):
            nonlocal called
            called = True
            del method, url, kwargs
            raise AssertionError("request should not be called when circuit is open")

    cb = _CircuitBreakerStub()
    monkeypatch.setattr(proxy_module, "get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=True))
    monkeypatch.setattr(proxy_module, "get_circuit_breaker_for_account", lambda _aid, _settings: cb)

    with pytest.raises(proxy_module.CircuitBreakerOpenError):
        await proxy_module._open_upstream_websocket(
            session=cast(proxy_module.aiohttp.ClientSession, _Session()),
            url="wss://chatgpt.com/backend-api/codex/responses",
            headers={"Authorization": "Bearer token"},
            connect_timeout_seconds=8.0,
            max_msg_size=1024,
            account_id="acc_test",
        )

    assert called is False
    assert cb.failures == []
    assert cb.successes == 0


@pytest.mark.asyncio
async def test_open_upstream_websocket_malformed_101_records_failure(monkeypatch):
    class _CircuitBreakerStub:
        def __init__(self) -> None:
            self.state = CircuitState.CLOSED
            self.failures: list[Exception] = []
            self.successes = 0

        async def pre_call_check(self) -> bool:
            return False

        async def release_half_open_probe(self) -> None:
            pass

        async def _record_failure(self, exc: Exception) -> None:
            self.failures.append(exc)

        async def _record_success(self) -> None:
            self.successes += 1

    class _Malformed101Response:
        def __init__(self) -> None:
            self.status = 101
            self.headers = {"Upgrade": "WRONG", "Connection": "Upgrade"}
            self.request_info = SimpleNamespace(real_url="wss://chatgpt.com/backend-api/codex/responses")
            self.history = ()
            self.connection = None

        async def text(self) -> str:
            return ""

        def close(self) -> None:
            return None

    class _Malformed101Session:
        def __init__(self) -> None:
            self._loop = asyncio.get_running_loop()
            self._ws_response_class = proxy_module.aiohttp.ClientWebSocketResponse

        async def request(self, method, url, **kwargs):
            del method, url, kwargs
            return _Malformed101Response()

    cb = _CircuitBreakerStub()
    monkeypatch.setattr(proxy_module, "get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=True))
    monkeypatch.setattr(proxy_module, "get_circuit_breaker_for_account", lambda _aid, _settings: cb)

    with pytest.raises(proxy_module.aiohttp.WSServerHandshakeError):
        await proxy_module._open_upstream_websocket(
            session=cast(proxy_module.aiohttp.ClientSession, _Malformed101Session()),
            url="wss://chatgpt.com/backend-api/codex/responses",
            headers={"Authorization": "Bearer token"},
            connect_timeout_seconds=8.0,
            max_msg_size=1024,
            account_id="acc_test",
        )

    assert cb.successes == 0
    assert len(cb.failures) == 1


@pytest.mark.asyncio
async def test_stream_responses_auto_transport_uses_model_preference(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "auto"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False

    registry = SimpleNamespace(
        get_snapshot=lambda: SimpleNamespace(models={"gpt-5.4": SimpleNamespace(prefer_websockets=True)})
    )

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "get_model_registry", lambda: registry)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    websocket = _WsResponse(
        [
            SimpleNamespace(
                type=proxy_module.aiohttp.WSMsgType.TEXT,
                data='{"type":"response.completed","response":{"id":"resp_auto"}}',
            )
        ]
    )
    session = _WsSession(websocket)
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    assert session.ws_calls
    assert events == [
        'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_auto"}}\n\n'
    ]


@pytest.mark.asyncio
async def test_stream_responses_auto_transport_uses_bootstrap_model_preference_when_registry_unloaded(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "auto"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(
        proxy_module,
        "get_model_registry",
        lambda: SimpleNamespace(prefers_websockets=lambda model: model == "gpt-5.4"),
    )
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    websocket = _WsResponse(
        [
            SimpleNamespace(
                type=proxy_module.aiohttp.WSMsgType.TEXT,
                data='{"type":"response.completed","response":{"id":"resp_auto_bootstrap"}}',
            )
        ]
    )
    session = _WsSession(websocket)
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    assert session.ws_calls
    assert not getattr(session, "post_calls", [])
    assert events == [
        'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_auto_bootstrap"}}\n\n'
    ]


@pytest.mark.asyncio
async def test_stream_responses_auto_transport_prefers_http_for_image_generation_tool(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "auto"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(
        proxy_module,
        "get_model_registry",
        lambda: SimpleNamespace(prefers_websockets=lambda model: model == "gpt-5.4"),
    )
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    session = _SseSession(
        _SsePostResponse([b'data: {"type":"response.completed","response":{"id":"resp_http_image_tool"}}\n\n'])
    )
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "draw",
            "input": [{"role": "user", "content": "draw"}],
            "tools": [{"type": "image_generation"}],
        }
    )

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={"originator": "codex_chatgpt_desktop"},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    assert session.calls
    assert not getattr(session, "ws_calls", [])
    assert events == ['data: {"type":"response.completed","response":{"id":"resp_http_image_tool"}}\n\n']


@pytest.mark.asyncio
async def test_stream_responses_http_transport_keeps_http(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False
        upstream_stream_transport = "http"

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(
        proxy_module,
        "get_model_registry",
        lambda: SimpleNamespace(prefers_websockets=lambda model: model == "gpt-5.4"),
    )
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    session = _SseSession(
        _SsePostResponse([b'data: {"type":"response.completed","response":{"id":"resp_http_legacy"}}\n\n'])
    )
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    assert session.calls
    assert events == ['data: {"type":"response.completed","response":{"id":"resp_http_legacy"}}\n\n']


@pytest.mark.asyncio
async def test_stream_responses_auto_transport_keeps_http_for_bare_session_affinity(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "auto"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False

    registry = SimpleNamespace(get_snapshot=lambda: SimpleNamespace(models={}))

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "get_model_registry", lambda: registry)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    session = _SseSession(_SsePostResponse([b'data: {"type":"response.completed","response":{"id":"resp_http"}}\n\n']))
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={"session_id": "sid-affinity-only"},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    assert session.calls
    assert events == ['data: {"type":"response.completed","response":{"id":"resp_http"}}\n\n']


@pytest.mark.asyncio
async def test_stream_responses_auto_transport_falls_back_to_http_when_websocket_upgrade_required(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "auto"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False

    registry = SimpleNamespace(
        get_snapshot=lambda: SimpleNamespace(models={"gpt-5.4": SimpleNamespace(prefer_websockets=True)})
    )
    attempts = {"websocket": 0}
    request_info = cast(RequestInfo, SimpleNamespace(real_url="wss://chatgpt.com/backend-api/codex/responses"))

    async def fake_open_upstream_websocket(**kwargs):
        attempts["websocket"] += 1
        raise proxy_module.aiohttp.WSServerHandshakeError(request_info, (), status=426, message="Upgrade Required")

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "get_model_registry", lambda: registry)
    monkeypatch.setattr(proxy_module, "_open_upstream_websocket", fake_open_upstream_websocket)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    session = _SseSession(_SsePostResponse([b'data: {"type":"response.completed","response":{"id":"resp_http"}}\n\n']))
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    assert attempts["websocket"] == 1
    assert session.calls
    assert events == ['data: {"type":"response.completed","response":{"id":"resp_http"}}\n\n']


@pytest.mark.asyncio
async def test_stream_responses_auto_transport_does_not_hide_forbidden_websocket_handshake(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "auto"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False

    registry = SimpleNamespace(
        get_snapshot=lambda: SimpleNamespace(models={"gpt-5.4": SimpleNamespace(prefer_websockets=True)})
    )
    request_info = cast(RequestInfo, SimpleNamespace(real_url="wss://chatgpt.com/backend-api/codex/responses"))

    async def fake_open_upstream_websocket(**kwargs):
        raise proxy_module.aiohttp.WSServerHandshakeError(request_info, (), status=403, message="Forbidden")

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "get_model_registry", lambda: registry)
    monkeypatch.setattr(proxy_module, "_open_upstream_websocket", fake_open_upstream_websocket)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    session = _SseSession(_SsePostResponse([b'data: {"type":"response.completed","response":{"id":"resp_http"}}\n\n']))
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    assert not session.calls
    event = json.loads(events[0].split("data: ", 1)[1])
    assert event["response"]["error"]["code"] == "upstream_error"


@pytest.mark.asyncio
async def test_stream_responses_uses_websocket_upstream_when_forced(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        log_upstream_request_summary = False
        proxy_request_budget_seconds = 75.0
        upstream_stream_transport = "websocket"
        upstream_websocket_mode = "force"

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [{"role": "user", "content": "hi"}],
            "service_tier": "priority",
        }
    )
    messages = [
        _WsMessage(
            proxy_module.aiohttp.WSMsgType.TEXT,
            json.dumps({"type": "response.created", "response": {"id": "resp_ws", "service_tier": "auto"}}),
        ),
        _WsMessage(
            proxy_module.aiohttp.WSMsgType.TEXT,
            json.dumps({"type": "response.completed", "response": {"id": "resp_ws", "service_tier": "default"}}),
        ),
    ]
    response = _WsResponse(messages)
    session = _WsSession(response)

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={"originator": "Codex Desktop", "session_id": "sid-1"},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    assert not session.post_calls
    assert session.ws_calls
    assert session.ws_calls[0]["url"] == "wss://chatgpt.com/backend-api/codex/responses"
    expected_payload = {"type": "response.create", **payload.to_payload()}
    expected_payload.pop("stream", None)
    assert response.sent_json == [expected_payload]
    expected_created = (
        "event: response.created\ndata: "
        '{"type":"response.created","response":{"id":"resp_ws","service_tier":"auto"}}\n\n'
    )
    expected_completed = (
        "event: response.completed\ndata: "
        '{"type":"response.completed","response":{"id":"resp_ws","service_tier":"default"}}\n\n'
    )
    assert events == [
        expected_created,
        expected_completed,
    ]


@pytest.mark.asyncio
async def test_stream_responses_forced_websocket_does_not_fallback_on_handshake_rejection(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "websocket"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        log_upstream_request_summary = False
        proxy_request_budget_seconds = 75.0

    request_info = cast(RequestInfo, SimpleNamespace(real_url="wss://chatgpt.com/backend-api/codex/responses"))

    async def fake_open_upstream_websocket(**kwargs):
        raise proxy_module.aiohttp.WSServerHandshakeError(request_info, (), status=403, message="Forbidden")

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_open_upstream_websocket", fake_open_upstream_websocket)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    session = _SseSession(_SsePostResponse([b'data: {"type":"response.completed","response":{"id":"resp_http"}}\n\n']))
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    assert not session.calls
    event = json.loads(events[0].split("data: ", 1)[1])
    assert event["response"]["error"]["code"] == "upstream_error"


@pytest.mark.asyncio
async def test_stream_responses_forced_websocket_preserves_rate_limit_code_on_handshake_rejection(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "websocket"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        log_upstream_request_summary = False
        proxy_request_budget_seconds = 75.0

    request_info = cast(RequestInfo, SimpleNamespace(real_url="wss://chatgpt.com/backend-api/codex/responses"))

    async def fake_open_upstream_websocket(**kwargs):
        raise proxy_module.aiohttp.WSServerHandshakeError(request_info, (), status=429, message="Too Many Requests")

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_open_upstream_websocket", fake_open_upstream_websocket)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        _ = [
            event
            async for event in proxy_module.stream_responses(
                payload,
                headers={},
                access_token="token",
                account_id="acc_1",
                session=cast(proxy_module.aiohttp.ClientSession, _SseSession(_SsePostResponse([]))),
                raise_for_status=True,
            )
        ]

    assert _proxy_error_code(exc_info.value) == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_stream_responses_forced_websocket_preserves_quota_code_from_handshake_error_payload(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "websocket"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        log_upstream_request_summary = False
        proxy_request_budget_seconds = 75.0

    request_info = cast(RequestInfo, SimpleNamespace(real_url="wss://chatgpt.com/backend-api/codex/responses"))
    error_payload = json.dumps(
        {"error": {"message": "quota exhausted", "type": "server_error", "code": "insufficient_quota"}}
    )

    async def fake_open_upstream_websocket(**kwargs):
        raise proxy_module.aiohttp.WSServerHandshakeError(request_info, (), status=403, message=error_payload)

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_open_upstream_websocket", fake_open_upstream_websocket)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, _SseSession(_SsePostResponse([]))),
        )
    ]

    event = json.loads(events[0].split("data: ", 1)[1])
    assert event["response"]["error"]["code"] == "insufficient_quota"


@pytest.mark.asyncio
async def test_stream_responses_uses_websocket_upstream_in_auto_mode_for_preferred_model(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        log_upstream_request_summary = False
        proxy_request_budget_seconds = 75.0
        upstream_stream_transport = "auto"
        upstream_websocket_mode = "auto"

    snapshot = SimpleNamespace(
        models={
            "gpt-5.4": SimpleNamespace(prefer_websockets=True),
        }
    )

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "get_model_registry", lambda: SimpleNamespace(get_snapshot=lambda: snapshot))
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [{"role": "user", "content": "hi"}],
        }
    )
    response = _WsResponse(
        [
            _WsMessage(
                proxy_module.aiohttp.WSMsgType.TEXT,
                json.dumps({"type": "response.completed", "response": {"id": "resp_auto"}}),
            )
        ]
    )
    session = _WsSession(response)

    _ = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    assert session.ws_calls
    assert not session.post_calls


@pytest.mark.asyncio
async def test_stream_responses_websocket_emits_incomplete_when_upstream_closes_without_terminal(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        log_upstream_request_summary = False
        proxy_request_budget_seconds = 75.0
        upstream_stream_transport = "websocket"
        upstream_websocket_mode = "force"

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )
    session = _WsSession(
        _WsResponse(
            [
                _WsMessage(
                    proxy_module.aiohttp.WSMsgType.TEXT,
                    json.dumps({"type": "response.created", "response": {"id": "resp_ws"}}),
                )
            ]
        )
    )

    events = [
        event
        async for event in proxy_module.stream_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    ]

    terminal = json.loads(events[-1].split("data: ", 1)[1])
    assert terminal["response"]["error"]["code"] == "stream_incomplete"


@pytest.mark.asyncio
async def test_compact_responses_starts_upstream_timer_after_image_inlining(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 1.0
        upstream_compact_timeout_seconds = 12.0
        image_inline_fetch_enabled = True
        log_upstream_request_payload = False

    inline_ran = False
    recorded: dict[str, float | None] = {}

    async def fake_inline(payload_dict, session, connect_timeout):
        nonlocal inline_ran
        inline_ran = True
        return payload_dict

    monotonic_values = iter([200.0, 205.5, 205.5, 205.5])

    def fake_monotonic():
        return next(monotonic_values, 205.5)

    def fake_complete(**kwargs):
        recorded["started_at"] = kwargs["started_at"]

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_inline_input_image_urls", fake_inline)
    monkeypatch.setattr(proxy_module.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", fake_complete)

    payload = proxy_module.ResponsesCompactRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )
    session = _CompactSession(
        _JsonCompactResponse(
            {"object": "response.compaction", "compaction_summary": {"encrypted_content": "enc_summary_1"}}
        )
    )

    result = await proxy_module.compact_responses(
        payload,
        headers={},
        access_token="token",
        account_id="acc_1",
        session=cast(proxy_module.aiohttp.ClientSession, session),
    )

    timeout = session.calls[0]["timeout"]
    assert isinstance(timeout, proxy_module.aiohttp.ClientTimeout)
    assert timeout.total == pytest.approx(6.5)
    assert timeout.sock_connect == pytest.approx(0.001)
    assert timeout.sock_read == pytest.approx(6.5)
    dumped = result.model_dump(mode="json", exclude_none=True)
    assert dumped["object"] == "response.compaction"
    assert dumped["compaction_summary"]["encrypted_content"] == "enc_summary_1"
    assert recorded["started_at"] == 205.5


@pytest.mark.asyncio
async def test_compact_responses_uses_configured_timeout_and_maps_read_timeout(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 2.0
        upstream_compact_timeout_seconds = 123.0
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False

    class _TimeoutCompactResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self, *, content_type=None):
            raise proxy_module.aiohttp.SocketTimeoutError("Timeout on reading data from socket")

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = proxy_module.ResponsesCompactRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )
    session = _CompactSession(_TimeoutCompactResponse())

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await proxy_module.compact_responses(
            payload,
            headers={},
            access_token="token",
            account_id="acc_1",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )

    timeout = session.calls[0]["timeout"]
    assert isinstance(timeout, proxy_module.aiohttp.ClientTimeout)
    assert timeout.total == pytest.approx(123.0, abs=0.05)
    assert timeout.sock_connect == pytest.approx(2.0, abs=0.05)
    assert timeout.sock_read == pytest.approx(123.0, abs=0.05)
    exc = _assert_proxy_response_error(exc_info.value)
    assert exc.status_code == 502
    assert _proxy_error_code(exc) == "upstream_unavailable"
    assert _proxy_error_message(exc) == "Timeout on reading data from socket"


@pytest.mark.asyncio
async def test_compact_responses_defaults_to_no_request_timeout(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 2.0
        upstream_compact_timeout_seconds = None
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    payload = proxy_module.ResponsesCompactRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )
    session = _CompactSession(
        _JsonCompactResponse(
            {"object": "response.compaction", "compaction_summary": {"encrypted_content": "enc_summary_2"}}
        )
    )

    result = await proxy_module.compact_responses(
        payload,
        headers={},
        access_token="token",
        account_id="acc_1",
        session=cast(proxy_module.aiohttp.ClientSession, session),
    )

    timeout = session.calls[0]["timeout"]
    assert isinstance(timeout, proxy_module.aiohttp.ClientTimeout)
    assert timeout.total is None
    assert timeout.sock_connect == pytest.approx(2.0, abs=0.05)
    assert timeout.sock_read is None
    dumped = result.model_dump(mode="json", exclude_none=True)
    assert dumped["object"] == "response.compaction"
    assert dumped["compaction_summary"]["encrypted_content"] == "enc_summary_2"


def test_sticky_key_for_responses_request_uses_bounded_cache_affinity():
    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})
    payload.prompt_cache_key = "thread_123"

    policy = proxy_service._sticky_key_for_responses_request(
        payload,
        headers={},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
    )

    assert policy.key == "thread_123"
    assert policy.kind == proxy_service.StickySessionKind.PROMPT_CACHE
    assert policy.reallocate_sticky is False
    assert policy.max_age_seconds == 300


def test_sticky_key_for_responses_request_keeps_sticky_threads_durable():
    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})
    payload.prompt_cache_key = "thread_123"

    policy = proxy_service._sticky_key_for_responses_request(
        payload,
        headers={},
        codex_session_affinity=False,
        openai_cache_affinity=False,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=True,
    )

    assert policy.key == "thread_123"
    assert policy.kind == proxy_service.StickySessionKind.STICKY_THREAD
    assert policy.reallocate_sticky is True
    assert policy.max_age_seconds is None


def test_sticky_key_for_compact_request_prefers_codex_session_affinity():
    payload = ResponsesCompactRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [],
            "prompt_cache_key": "thread_123",
        }
    )

    policy = proxy_service._sticky_key_for_compact_request(
        payload,
        headers={"session_id": "codex-session-1"},
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=True,
    )

    assert policy.key == "codex-session-1"
    assert policy.kind == proxy_service.StickySessionKind.CODEX_SESSION
    assert policy.reallocate_sticky is False
    assert policy.max_age_seconds is None


def test_sticky_key_from_session_header_accepts_aliases_in_priority_order():
    assert proxy_service._sticky_key_from_session_header({"session_id": "sid_1"}) == "sid_1"
    assert proxy_service._sticky_key_from_session_header({"x-codex-session-id": "sid_2"}) == "sid_2"
    assert proxy_service._sticky_key_from_session_header({"x-codex-conversation-id": "sid_3"}) == "sid_3"
    assert (
        proxy_service._sticky_key_from_session_header(
            {
                "x-codex-conversation-id": "sid_3",
                "x-codex-session-id": "sid_2",
                "session_id": "sid_1",
            }
        )
        == "sid_1"
    )


def test_owner_lookup_session_id_from_headers_prefers_turn_state_then_session_aliases():
    assert proxy_service._owner_lookup_session_id_from_headers({"x-codex-turn-state": "turn_1"}) == "turn_1"
    assert (
        proxy_service._owner_lookup_session_id_from_headers({"x-codex-turn-state": "turn_1", "session_id": "sid_1"})
        == "turn_1"
    )
    assert proxy_service._owner_lookup_session_id_from_headers({"x-codex-session-id": "sid_2"}) == "sid_2"
    assert proxy_service._owner_lookup_session_id_from_headers({"x-codex-conversation-id": "sid_3"}) == "sid_3"
    assert proxy_service._owner_lookup_session_id_from_headers({}) is None


def test_sticky_key_for_responses_request_derives_prompt_cache_before_codex_session_return():
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            "stream": True,
        }
    )

    policy = proxy_service._sticky_key_for_responses_request(
        payload,
        headers={"session_id": "codex-session-1"},
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
    )

    assert policy.key == "codex-session-1"
    assert policy.kind == proxy_service.StickySessionKind.CODEX_SESSION
    assert isinstance(payload.prompt_cache_key, str)
    assert payload.prompt_cache_key


def test_sticky_key_for_compact_request_derives_prompt_cache_before_codex_session_return():
    payload = ResponsesCompactRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
        }
    )

    policy = proxy_service._sticky_key_for_compact_request(
        payload,
        headers={"session_id": "codex-session-1"},
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
    )

    assert policy.key == "codex-session-1"
    assert policy.kind == proxy_service.StickySessionKind.CODEX_SESSION
    assert isinstance(payload.prompt_cache_key, str)
    assert payload.prompt_cache_key


def test_sticky_key_for_responses_request_respects_prompt_cache_derivation_flag(monkeypatch):
    class Settings:
        openai_prompt_cache_key_derivation_enabled = False

    monkeypatch.setattr(proxy_service, "get_settings", lambda: Settings())

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            "stream": True,
        }
    )

    policy = proxy_service._sticky_key_for_responses_request(
        payload,
        headers={"session_id": "codex-session-1"},
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
    )

    assert policy.kind == proxy_service.StickySessionKind.CODEX_SESSION
    assert payload.prompt_cache_key is None


def test_sticky_key_for_responses_request_preserves_client_supplied_prompt_cache_key_when_flag_off(monkeypatch):
    class Settings:
        openai_prompt_cache_key_derivation_enabled = False

    monkeypatch.setattr(proxy_service, "get_settings", lambda: Settings())

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            "stream": True,
            "prompt_cache_key": "thread_123",
        }
    )

    policy = proxy_service._sticky_key_for_responses_request(
        payload,
        headers={"session_id": "codex-session-1"},
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
    )

    assert policy.kind == proxy_service.StickySessionKind.CODEX_SESSION
    assert payload.prompt_cache_key == "thread_123"


def test_sticky_key_for_responses_request_strips_whitespace_before_accepting_payload_key():
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            "stream": True,
            "prompt_cache_key": "  thread_123  ",
        }
    )

    policy = proxy_service._sticky_key_for_responses_request(
        payload,
        headers={},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
    )

    assert policy.kind == proxy_service.StickySessionKind.PROMPT_CACHE
    assert policy.key == "thread_123"
    assert payload.prompt_cache_key == "thread_123"


def test_sticky_key_for_responses_request_derives_when_payload_key_is_whitespace_only():
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            "stream": True,
            "prompt_cache_key": "   ",
        }
    )

    policy = proxy_service._sticky_key_for_responses_request(
        payload,
        headers={},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
    )

    assert policy.kind == proxy_service.StickySessionKind.PROMPT_CACHE
    assert isinstance(policy.key, str)
    assert policy.key
    assert payload.prompt_cache_key == policy.key


@pytest.mark.asyncio
async def test_service_compact_budget_does_not_override_unbounded_read_timeout(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_compact_unbounded_read")
    runtime_values = dict(settings.__dict__)
    runtime_values["compact_request_budget_seconds"] = 3.0
    runtime_settings = SimpleNamespace(**runtime_values)
    captured: dict[str, float | None] = {}

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(runtime_settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: runtime_settings)
    monkeypatch.setattr(proxy_service.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(service, "_settle_compact_api_key_usage", AsyncMock())

    async def fake_compact(payload, headers, access_token, account_id):
        captured["connect_timeout"] = proxy_module._COMPACT_CONNECT_TIMEOUT_OVERRIDE.get()
        captured["total_timeout"] = proxy_module._COMPACT_TOTAL_TIMEOUT_OVERRIDE.get()
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_service, "core_compact_responses", fake_compact)

    payload = ResponsesCompactRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": []})

    result = await service.compact_responses(payload, {"session_id": "sid-compact"})

    assert captured["connect_timeout"] == pytest.approx(3.0)
    assert captured["total_timeout"] is None
    assert result.model_extra == {"output": []}


def test_logged_error_json_response_emits_proxy_error_log(caplog):
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/responses",
        "raw_path": b"/v1/responses",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 2455),
    }
    request = Request(scope)

    token = set_request_id("req_proxy_error_1")
    try:
        caplog.set_level(logging.WARNING)
        response = proxy_api._logged_error_json_response(
            request,
            502,
            {"error": {"code": "upstream_error", "message": "provider failed"}},
        )
    finally:
        reset_request_id(token)

    assert response.status_code == 502
    assert "proxy_error_response request_id=req_proxy_error_1" in caplog.text
    assert "code=upstream_error" in caplog.text
    assert "message=provider failed" in caplog.text


@pytest.mark.asyncio
async def test_stream_responses_logs_actual_service_tier_and_requested_tier_trace(monkeypatch, caplog):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=True)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_trace_stream")

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(service, "_settle_stream_api_key_usage", AsyncMock(return_value=True))

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield 'data: {"type":"response.completed","response":{"id":"resp_trace_stream","service_tier":"default"}}\n\n'

    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [],
            "stream": True,
            "service_tier": "priority",
        }
    )

    token = set_request_id(None)
    try:
        caplog.set_level(logging.WARNING)
        chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]
        request_id = get_request_id()
    finally:
        reset_request_id(token)

    assert chunks
    assert request_id
    assert request_logs.calls[0]["service_tier"] == "default"
    assert request_logs.calls[0]["requested_service_tier"] == "priority"
    assert request_logs.calls[0]["actual_service_tier"] == "default"
    assert f"request_id={request_id}" in caplog.text
    assert "kind=stream" in caplog.text
    assert "requested_service_tier=priority" in caplog.text
    assert "actual_service_tier=default" in caplog.text


@pytest.mark.asyncio
async def test_service_stream_responses_uses_dashboard_upstream_transport_override(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    setattr(settings, "upstream_stream_transport", "websocket")
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_stream_transport_override")
    captured: dict[str, object] = {}

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(service, "_settle_stream_api_key_usage", AsyncMock(return_value=True))

    async def fake_stream(
        payload,
        headers,
        access_token,
        account_id,
        base_url=None,
        raise_for_status=False,
        upstream_stream_transport_override=None,
    ):
        captured["override"] = upstream_stream_transport_override
        yield 'data: {"type":"response.completed","response":{"id":"resp_transport_override"}}\n\n'

    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [],
            "stream": True,
        }
    )

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    assert chunks
    assert captured["override"] == "websocket"


@pytest.mark.asyncio
async def test_service_stream_responses_does_not_infer_previous_response_id_from_session_scope(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    request_logs.latest_response_by_session[("turn_stream_scope", None)] = "resp_latest_scope"
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_stream_no_session_infer")
    captured: dict[str, str | None] = {}

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(service, "_settle_stream_api_key_usage", AsyncMock(return_value=True))

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        del headers, access_token, account_id, base_url, raise_for_status
        captured["previous_response_id"] = payload.previous_response_id
        yield 'data: {"type":"response.completed","response":{"id":"resp_stream_scope"}}\n\n'

    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "continue"}]}],
            "stream": True,
        }
    )

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "turn_stream_scope"})]

    assert chunks
    assert captured["previous_response_id"] is None
    assert request_logs.session_lookup_calls == []


@pytest.mark.asyncio
async def test_compact_responses_logs_service_tier_trace_and_generates_request_id(monkeypatch, caplog):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=True)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_trace_compact")

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(service, "_settle_compact_api_key_usage", AsyncMock())

    async def fake_compact(payload, headers, access_token, account_id):
        return OpenAIResponsePayload.model_validate({"output": [], "service_tier": "default"})

    monkeypatch.setattr(proxy_service, "core_compact_responses", fake_compact)

    payload = ResponsesCompactRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "summarize",
            "input": [],
            "service_tier": "priority",
        }
    )

    token = set_request_id(None)
    try:
        caplog.set_level(logging.WARNING)
        response = await service.compact_responses(payload, {"session_id": "sid-compact"}, codex_session_affinity=True)
        request_id = get_request_id()
    finally:
        reset_request_id(token)

    assert proxy_service._service_tier_from_response(response) == "default"
    assert request_logs.calls[0]["service_tier"] == "default"
    assert request_logs.calls[0]["requested_service_tier"] == "priority"
    assert request_logs.calls[0]["actual_service_tier"] == "default"
    assert request_id
    assert f"request_id={request_id}" in caplog.text
    assert "kind=compact" in caplog.text
    assert "requested_service_tier=priority" in caplog.text
    assert "actual_service_tier=default" in caplog.text
    assert request_logs.calls[0]["transport"] == "http"


@pytest.mark.asyncio
async def test_compact_responses_does_not_infer_previous_response_id_from_session_scope(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    request_logs.latest_response_by_session[("turn_compact_scope", None)] = "resp_latest_scope"
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_compact_no_session_infer")
    captured: dict[str, str | None] = {}

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(service, "_settle_compact_api_key_usage", AsyncMock())

    async def fake_compact(payload, headers, access_token, account_id):
        del headers, access_token, account_id
        captured["previous_response_id"] = getattr(payload, "previous_response_id", None)
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_service, "core_compact_responses", fake_compact)

    payload = ResponsesCompactRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "summarize",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "continue"}]}],
        }
    )

    result = await service.compact_responses(payload, {"session_id": "turn_compact_scope"})

    assert result.model_extra == {"output": []}
    assert captured["previous_response_id"] is None
    assert request_logs.session_lookup_calls == []


@pytest.mark.asyncio
async def test_stream_responses_propagates_selection_error_code(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(
            return_value=AccountSelection(
                account=None,
                error_message="No fresh additional quota data available for model 'gpt-5.3-codex-spark'",
                error_code="additional_quota_data_unavailable",
            )
        ),
    )

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.3-codex-spark",
            "instructions": "hi",
            "input": [],
            "stream": True,
        }
    )

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    event = json.loads(chunks[0].split("data: ", 1)[1])
    assert event["response"]["error"]["code"] == "additional_quota_data_unavailable"
    assert request_logs.calls[0]["error_code"] == "additional_quota_data_unavailable"


@pytest.mark.asyncio
async def test_stream_responses_non_retryable_first_failure_does_not_retry(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_no_retry")
    record_error = AsyncMock()
    record_success = AsyncMock()

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    select_account = AsyncMock(return_value=AccountSelection(account=account, error_message=None))
    monkeypatch.setattr(service._load_balancer, "select_account", select_account)
    monkeypatch.setattr(service._load_balancer, "record_error", record_error)
    monkeypatch.setattr(service._load_balancer, "record_success", record_success)
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield (
            'data: {"type":"response.failed","response":{"error":{"code":"stream_idle_timeout","message":"idle"}}}\n\n'
        )

    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)

    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    event = json.loads(chunks[0].split("data: ", 1)[1])
    assert event["response"]["error"]["code"] == "stream_idle_timeout"
    assert select_account.await_count == 1
    record_error.assert_not_awaited()
    record_success.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_responses_retries_security_work_warning_on_authorized_account(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    regular_account = _make_account("acc_regular_security")
    authorized_account = _make_account("acc_authorized_security")
    authorized_account.security_work_authorized = True
    cyber_message = (
        "This chat was flagged for possible cybersecurity risk. "
        "If this seems wrong, try rephrasing your request. "
        "To get authorized for security work, join the Trusted Access for Cyber program. "
        "https://chatgpt.com/cyber"
    )
    select_account = AsyncMock(
        side_effect=[
            AccountSelection(account=regular_account, error_message=None),
            AccountSelection(account=authorized_account, error_message=None),
        ]
    )
    record_error = AsyncMock()

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(service._load_balancer, "select_account", select_account)
    monkeypatch.setattr(service._load_balancer, "record_error", record_error)
    monkeypatch.setattr(service._load_balancer, "record_success", AsyncMock())
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(side_effect=lambda account, **kwargs: account))

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        del payload, headers, access_token, base_url, raise_for_status
        if account_id == regular_account.chatgpt_account_id:
            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "response.failed",
                        "response": {
                            "id": "resp_cyber_fail",
                            "error": {
                                "code": "invalid_request_error",
                                "type": "invalid_request_error",
                                "message": cyber_message,
                            },
                        },
                    }
                )
                + "\n\n"
            )
            return
        yield (
            'data: {"type":"response.completed","response":'
            '{"id":"resp_ok","usage":{"input_tokens":1,"output_tokens":1}}}\n\n'
        )

    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)

    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    assert len(chunks) == 1
    event = json.loads(chunks[0].split("data: ", 1)[1])
    assert event["type"] == "response.completed"
    assert select_account.await_count == 2
    first_call, second_call = select_account.await_args_list
    assert first_call.kwargs["require_security_work_authorized"] is False
    assert second_call.kwargs["require_security_work_authorized"] is True
    assert second_call.kwargs["exclude_account_ids"] == {regular_account.id}
    assert [call["account_id"] for call in request_logs.calls] == [
        regular_account.id,
        authorized_account.id,
    ]
    assert request_logs.calls[0]["error_code"] == "security_work_authorization_required"
    assert request_logs.calls[1]["status"] == "success"


@pytest.mark.asyncio
async def test_stream_responses_does_not_move_file_pinned_security_work_request(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    regular_account = _make_account("acc_regular_security_file")
    authorized_account = _make_account("acc_authorized_security_file")
    authorized_account.security_work_authorized = True
    cyber_message = (
        "This chat was flagged for possible cybersecurity risk. "
        "To get authorized for security work, join the Trusted Access for Cyber program. "
        "https://chatgpt.com/cyber"
    )
    select_account = AsyncMock(
        side_effect=[
            AccountSelection(account=regular_account, error_message=None),
            AccountSelection(account=authorized_account, error_message=None),
        ]
    )

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(service._load_balancer, "select_account", select_account)
    monkeypatch.setattr(service._load_balancer, "record_success", AsyncMock())
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(side_effect=lambda account, **kwargs: account))
    monkeypatch.setattr(service, "_resolve_file_account_for_responses", AsyncMock(return_value=regular_account.id))

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        del payload, headers, access_token, base_url, raise_for_status
        assert account_id == regular_account.chatgpt_account_id
        yield (
            "data: "
            + json.dumps(
                {
                    "type": "response.failed",
                    "response": {
                        "id": "resp_cyber_file",
                        "error": {
                            "code": "invalid_request_error",
                            "type": "invalid_request_error",
                            "message": cyber_message,
                        },
                    },
                }
            )
            + "\n\n"
        )

    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "check pinned file",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_file",
                            "file_id": "file_pinned",
                        }
                    ],
                }
            ],
            "stream": True,
        }
    )

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream-file"})]

    assert len(chunks) == 1
    event = json.loads(chunks[0].split("data: ", 1)[1])
    assert event["type"] == "response.failed"
    assert event["response"]["error"]["code"] == "security_work_authorization_required"
    assert select_account.await_count == 1
    only_call = select_account.await_args_list[0]
    assert only_call.kwargs["account_ids"] == {regular_account.id}
    assert only_call.kwargs["require_security_work_authorized"] is False


@pytest.mark.asyncio
async def test_http_bridge_retries_security_work_warning_on_authorized_account(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    regular_account = _make_account("acc_bridge_security_regular")
    authorized_account = _make_account("acc_bridge_security_authorized")
    authorized_account.security_work_authorized = True
    request_text = json.dumps(
        {
            "type": "response.create",
            "model": "gpt-5.1",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "check api"}]}],
        },
        separators=(",", ":"),
    )

    class _FakeUpstreamWebSocket:
        def __init__(self) -> None:
            self.sent_text: list[str] = []

        async def send_text(self, text: str) -> None:
            self.sent_text.append(text)

    retry_upstream = _FakeUpstreamWebSocket()
    reconnect_calls: list[dict[str, object]] = []

    async def fake_reconnect_http_bridge_session(
        session,
        *,
        request_state,
        restart_reader=False,
        require_security_work_authorized=False,
    ):
        reconnect_calls.append(
            {
                "request_state": request_state,
                "restart_reader": restart_reader,
                "require_security_work_authorized": require_security_work_authorized,
            }
        )
        session.account = authorized_account
        session.upstream = retry_upstream
        session.upstream_control = proxy_service._WebSocketUpstreamControl()

    monkeypatch.setattr(service, "_reconnect_http_bridge_session", fake_reconnect_http_bridge_session)

    request_state = proxy_service._WebSocketRequestState(
        request_id="bridge_req_security",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        transport="http",
        request_text=request_text,
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("turn_state_header", "turn-security", None),
        headers={},
        affinity=proxy_service._AffinityPolicy(),
        request_model="gpt-5.1",
        account=regular_account,
        upstream=cast(proxy_service.UpstreamResponsesWebSocket, _FakeUpstreamWebSocket()),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([request_state]),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=1,
        last_used_at=1.0,
        idle_ttl_seconds=300.0,
    )
    cyber_message = (
        "This chat was flagged for possible cybersecurity risk. "
        "To get authorized for security work, join the Trusted Access for Cyber program. "
        "https://chatgpt.com/cyber"
    )
    text = json.dumps(
        {
            "type": "response.failed",
            "response": {
                "id": "resp_security_failed",
                "status": "failed",
                "error": {
                    "code": "invalid_request_error",
                    "type": "invalid_request_error",
                    "message": cyber_message,
                },
            },
        },
        separators=(",", ":"),
    )

    await service._process_http_bridge_upstream_text(session, text)

    assert reconnect_calls == [
        {
            "request_state": request_state,
            "restart_reader": False,
            "require_security_work_authorized": True,
        }
    ]
    assert session.account is authorized_account
    assert retry_upstream.sent_text == [request_text]
    assert list(session.pending_requests) == [request_state]
    assert session.queued_request_count == 1
    assert request_state.replay_count == 1
    assert request_state.response_id is None
    assert request_state.awaiting_response_created is True
    assert request_state.event_queue is not None
    assert request_state.event_queue.empty()


@pytest.mark.asyncio
async def test_http_bridge_does_not_replay_security_work_warning_after_response_created(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    regular_account = _make_account("acc_bridge_security_created")
    request_text = json.dumps(
        {
            "type": "response.create",
            "model": "gpt-5.1",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "check api"}]}],
        },
        separators=(",", ":"),
    )
    reconnect = AsyncMock()
    monkeypatch.setattr(service, "_reconnect_http_bridge_session", reconnect)

    request_state = proxy_service._WebSocketRequestState(
        request_id="bridge_req_security_created",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=False,
        event_queue=asyncio.Queue(),
        transport="http",
        request_text=request_text,
    )
    request_state.response_id = "resp_security_created"
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("turn_state_header", "turn-security-created", None),
        headers={},
        affinity=proxy_service._AffinityPolicy(),
        request_model="gpt-5.1",
        account=regular_account,
        upstream=cast(proxy_service.UpstreamResponsesWebSocket, SimpleNamespace()),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([request_state]),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=1,
        last_used_at=1.0,
        idle_ttl_seconds=300.0,
    )
    cyber_message = (
        "This chat was flagged for possible cybersecurity risk. "
        "To get authorized for security work, join the Trusted Access for Cyber program. "
        "https://chatgpt.com/cyber"
    )
    text = json.dumps(
        {
            "type": "response.failed",
            "response": {
                "id": "resp_security_created",
                "status": "failed",
                "error": {
                    "code": "invalid_request_error",
                    "type": "invalid_request_error",
                    "message": cyber_message,
                },
            },
        },
        separators=(",", ":"),
    )

    await service._process_http_bridge_upstream_text(session, text)

    reconnect.assert_not_awaited()
    assert request_state.replay_count == 0
    assert list(session.pending_requests) == []
    assert request_state.event_queue is not None
    forwarded = await request_state.event_queue.get()
    assert forwarded is not None
    assert json.loads(forwarded.split("data: ", 1)[1])["type"] == "response.failed"
    assert await request_state.event_queue.get() is None


@pytest.mark.asyncio
async def test_compact_responses_retries_security_work_warning_on_authorized_account(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    regular_account = _make_account("acc_compact_security_regular")
    authorized_account = _make_account("acc_compact_security_authorized")
    authorized_account.security_work_authorized = True
    select_account = AsyncMock(
        side_effect=[
            AccountSelection(account=regular_account, error_message=None),
            AccountSelection(account=authorized_account, error_message=None),
        ]
    )
    record_error = AsyncMock()
    cyber_message = (
        "This chat was flagged for possible cybersecurity risk. "
        "To get authorized for security work, join the Trusted Access for Cyber program. "
        "https://chatgpt.com/cyber"
    )

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(service._load_balancer, "select_account", select_account)
    monkeypatch.setattr(service._load_balancer, "record_error", record_error)
    monkeypatch.setattr(service._load_balancer, "record_success", AsyncMock())
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(side_effect=lambda account, **kwargs: account))
    monkeypatch.setattr(service, "_settle_compact_api_key_usage", AsyncMock())

    async def fake_compact(payload, headers, access_token, account_id):
        del payload, headers, access_token
        if account_id == regular_account.chatgpt_account_id:
            raise proxy_module.ProxyResponseError(
                400,
                openai_error(
                    "invalid_request_error",
                    cyber_message,
                    error_type="invalid_request_error",
                ),
            )
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_service, "core_compact_responses", fake_compact)

    payload = ResponsesCompactRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": []})

    result = await service.compact_responses(payload, {"session_id": "sid-compact"})

    assert result.model_extra == {"output": []}
    assert select_account.await_count == 2
    first_call, second_call = select_account.await_args_list
    assert first_call.kwargs["require_security_work_authorized"] is False
    assert second_call.kwargs["require_security_work_authorized"] is True
    assert second_call.kwargs["exclude_account_ids"] == {regular_account.id}
    assert [call["account_id"] for call in request_logs.calls] == [authorized_account.id]
    assert request_logs.calls[0]["status"] == "success"
    record_error.assert_not_awaited()


@pytest.mark.asyncio
async def test_connect_proxy_websocket_passes_sticky_kind_to_load_balancer(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_ws_sticky")
    select_account = AsyncMock(return_value=AccountSelection(account=account, error_message=None))
    upstream = SimpleNamespace()

    monkeypatch.setattr(service._load_balancer, "select_account", select_account)
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(service, "_open_upstream_websocket", AsyncMock(return_value=upstream))

    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_1",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
    )

    websocket = cast(WebSocket, SimpleNamespace(send_text=AsyncMock()))
    selected_account, selected_upstream = await service._connect_proxy_websocket(
        {},
        sticky_key="codex-session-1",
        sticky_kind=proxy_service.StickySessionKind.CODEX_SESSION,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        model="gpt-5.1",
        request_state=request_state,
        api_key=None,
        client_send_lock=anyio.Lock(),
        websocket=websocket,
    )

    assert selected_account == account
    assert selected_upstream is upstream
    await_args = select_account.await_args
    assert await_args is not None
    assert await_args.kwargs["sticky_key"] == "codex-session-1"
    assert await_args.kwargs["sticky_kind"] == proxy_service.StickySessionKind.CODEX_SESSION


@pytest.mark.asyncio
async def test_connect_proxy_websocket_logs_preconnect_failure(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    select_account = AsyncMock(
        return_value=AccountSelection(
            account=None, error_message="No active accounts available", error_code="no_accounts"
        )
    )

    monkeypatch.setattr(service._load_balancer, "select_account", select_account)
    monkeypatch.setattr(service, "_release_websocket_reservation", AsyncMock())

    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_no_accounts",
        model="gpt-5.1",
        service_tier="default",
        reasoning_effort="high",
        api_key_reservation=None,
        started_at=0.0,
    )

    websocket = cast(WebSocket, SimpleNamespace(send_text=AsyncMock()))
    selected_account, selected_upstream = await service._connect_proxy_websocket(
        {},
        sticky_key=None,
        sticky_kind=None,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        model="gpt-5.1",
        request_state=request_state,
        api_key=None,
        client_send_lock=anyio.Lock(),
        websocket=websocket,
    )

    assert selected_account is None
    assert selected_upstream is None
    assert request_logs.calls[0]["request_id"] == "ws_req_no_accounts"
    assert request_logs.calls[0]["status"] == "error"
    assert request_logs.calls[0]["error_code"] == "no_accounts"
    assert request_logs.calls[0]["transport"] == "websocket"


@pytest.mark.asyncio
async def test_connect_proxy_websocket_maps_budget_exhaustion_to_timeout_error(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))

    monkeypatch.setattr(
        service,
        "_select_account_with_budget",
        AsyncMock(
            side_effect=proxy_module.ProxyResponseError(
                502,
                openai_error("upstream_unavailable", "Proxy request budget exhausted"),
            )
        ),
    )
    monkeypatch.setattr(service, "_release_websocket_reservation", AsyncMock())

    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_budget_timeout",
        model="gpt-5.1",
        service_tier="priority",
        reasoning_effort="high",
        api_key_reservation=None,
        started_at=100.0,
    )

    monkeypatch.setattr(proxy_service.time, "monotonic", lambda: 100.0)

    websocket_send = AsyncMock()
    websocket = cast(WebSocket, SimpleNamespace(send_text=websocket_send))
    selected_account, selected_upstream = await service._connect_proxy_websocket(
        {},
        sticky_key=None,
        sticky_kind=None,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        model="gpt-5.1",
        request_state=request_state,
        api_key=None,
        client_send_lock=anyio.Lock(),
        websocket=websocket,
    )

    assert selected_account is None
    assert selected_upstream is None
    await_args = websocket_send.await_args
    assert await_args is not None
    sent_payload = json.loads(await_args.args[0])
    assert sent_payload["status"] == 502
    assert sent_payload["error"]["code"] == "upstream_request_timeout"
    assert sent_payload["error"]["message"] == "Proxy request budget exhausted"
    assert request_logs.calls[0]["request_id"] == "ws_req_budget_timeout"
    assert request_logs.calls[0]["error_code"] == "upstream_request_timeout"
    assert request_logs.calls[0]["service_tier"] == "priority"


@pytest.mark.asyncio
async def test_connect_proxy_websocket_surfaces_retry_handshake_error(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_ws_retry_error")
    first_exc = proxy_module.ProxyResponseError(401, openai_error("invalid_api_key", "expired"))
    second_exc = proxy_module.ProxyResponseError(403, openai_error("forbidden", "denied"))
    handle_connect_error = AsyncMock()

    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(side_effect=[account, account]))
    monkeypatch.setattr(service, "_open_upstream_websocket", AsyncMock(side_effect=[first_exc, second_exc]))
    monkeypatch.setattr(service, "_handle_websocket_connect_error", handle_connect_error)
    monkeypatch.setattr(service, "_release_websocket_reservation", AsyncMock())

    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_retry_error",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
    )

    websocket_send = AsyncMock()
    websocket = cast(WebSocket, SimpleNamespace(send_text=websocket_send))
    selected_account, selected_upstream = await service._connect_proxy_websocket(
        {},
        sticky_key=None,
        sticky_kind=None,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        model="gpt-5.1",
        request_state=request_state,
        api_key=None,
        client_send_lock=anyio.Lock(),
        websocket=websocket,
    )

    assert selected_account is None
    assert selected_upstream is None
    await_args = handle_connect_error.await_args
    assert await_args is not None
    assert await_args.args[1] is second_exc
    websocket_await_args = websocket_send.await_args
    assert websocket_await_args is not None
    sent_payload = json.loads(websocket_await_args.args[0])
    assert sent_payload["status"] == 403
    assert sent_payload["error"]["code"] == "forbidden"
    assert request_logs.calls[0]["error_code"] == "forbidden"


@pytest.mark.asyncio
async def test_connect_proxy_websocket_fails_over_on_handshake_usage_limit_reached(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account_a = _make_account("acc_ws_failover_a")
    account_b = _make_account("acc_ws_failover_b")
    upstream = SimpleNamespace()

    select_account = AsyncMock(
        side_effect=[
            AccountSelection(account=account_a, error_message=None),
            AccountSelection(account=account_b, error_message=None),
        ]
    )
    mark_rate_limit = AsyncMock()
    first_handshake_error = proxy_module.ProxyResponseError(
        429,
        openai_error("usage_limit_reached", "usage limit reached"),
    )

    monkeypatch.setattr(service._load_balancer, "select_account", select_account)
    monkeypatch.setattr(service._load_balancer, "mark_rate_limit", mark_rate_limit)
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(side_effect=[account_a, account_b]))
    monkeypatch.setattr(service, "_open_upstream_websocket", AsyncMock(side_effect=[first_handshake_error, upstream]))
    monkeypatch.setattr(service, "_release_websocket_reservation", AsyncMock())

    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_failover_handshake_429",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
    )

    websocket_send = AsyncMock()
    websocket = cast(WebSocket, SimpleNamespace(send_text=websocket_send))
    selected_account, selected_upstream = await service._connect_proxy_websocket(
        {},
        sticky_key=None,
        sticky_kind=None,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        model="gpt-5.1",
        request_state=request_state,
        api_key=None,
        client_send_lock=anyio.Lock(),
        websocket=websocket,
    )

    assert selected_account == account_b
    assert selected_upstream is upstream
    assert select_account.await_count == 2
    first_call, second_call = select_account.await_args_list
    assert first_call.kwargs["exclude_account_ids"] == set()
    assert second_call.kwargs["exclude_account_ids"] == {account_a.id}
    mark_rate_limit.assert_awaited_once()
    mark_call = mark_rate_limit.await_args
    assert mark_call is not None
    assert mark_call.args[0] == account_a
    assert mark_call.args[1]["message"] == "usage limit reached"
    websocket_send.assert_not_awaited()
    assert request_logs.calls == []


@pytest.mark.asyncio
async def test_connect_proxy_websocket_previous_response_owner_usage_limit_fails_closed(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account_owner = _make_account("acc_ws_prev_owner")
    account_other = _make_account("acc_ws_other")
    seen_excluded_account_ids: list[set[str]] = []

    async def select_account(deadline: float, **kwargs: object) -> AccountSelection:
        del deadline
        excluded_account_ids = kwargs.get("exclude_account_ids")
        seen_excluded_account_ids.append(set(cast(set[str], excluded_account_ids)))
        if len(seen_excluded_account_ids) == 1:
            return AccountSelection(account=account_owner, error_message=None)
        return AccountSelection(account=account_other, error_message=None)

    mark_rate_limit = AsyncMock()
    first_handshake_error = proxy_module.ProxyResponseError(
        429,
        openai_error("usage_limit_reached", "usage limit reached"),
    )

    monkeypatch.setattr(service, "_select_account_with_budget_compatible", select_account)
    monkeypatch.setattr(service._load_balancer, "mark_rate_limit", mark_rate_limit)
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account_owner))
    monkeypatch.setattr(service, "_open_upstream_websocket", AsyncMock(side_effect=[first_handshake_error]))
    monkeypatch.setattr(service, "_release_websocket_reservation", AsyncMock())

    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_prev_owner_handshake_429",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        previous_response_id="resp_prev_anchor",
        preferred_account_id=account_owner.id,
    )

    websocket_send = AsyncMock()
    websocket = cast(WebSocket, SimpleNamespace(send_text=websocket_send))
    selected_account, selected_upstream = await service._connect_proxy_websocket(
        {},
        sticky_key=None,
        sticky_kind=None,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        model="gpt-5.1",
        request_state=request_state,
        api_key=None,
        client_send_lock=anyio.Lock(),
        websocket=websocket,
    )

    assert selected_account is None
    assert selected_upstream is None
    assert seen_excluded_account_ids == [set(), {account_owner.id}]
    mark_rate_limit.assert_awaited_once()
    mark_call = mark_rate_limit.await_args
    assert mark_call is not None
    assert mark_call.args[0] == account_owner
    assert mark_call.args[1]["message"] == "usage limit reached"
    await_args = websocket_send.await_args
    assert await_args is not None
    sent_payload = json.loads(await_args.args[0])
    assert sent_payload["status"] == 502
    assert sent_payload["error"]["code"] == "upstream_unavailable"
    assert sent_payload["error"]["message"] == "Previous response owner account is unavailable; retry later."
    assert request_logs.calls[0]["request_id"] == "ws_req_prev_owner_handshake_429"
    assert request_logs.calls[0]["error_code"] == "upstream_unavailable"
    assert request_logs.calls[0]["account_id"] == account_owner.id


@pytest.mark.asyncio
async def test_connect_proxy_websocket_surfaces_local_connect_overload_without_penalizing_account(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    settings.proxy_upstream_websocket_connect_limit = 1
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_ws_connect_overload")
    record_error = AsyncMock()
    release_reservation = AsyncMock()
    connect_upstream = AsyncMock()

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(service._load_balancer, "record_error", record_error)
    monkeypatch.setattr(service, "_release_websocket_reservation", release_reservation)
    monkeypatch.setattr(proxy_service, "connect_responses_websocket", connect_upstream)

    lease = await service._get_work_admission().acquire_websocket_connect()
    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_connect_overload",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
    )

    websocket_send = AsyncMock()
    websocket = cast(WebSocket, SimpleNamespace(send_text=websocket_send))
    try:
        selected_account, selected_upstream = await service._connect_proxy_websocket(
            {},
            sticky_key=None,
            sticky_kind=None,
            prefer_earlier_reset=False,
            routing_strategy="usage_weighted",
            model="gpt-5.1",
            request_state=request_state,
            api_key=None,
            client_send_lock=anyio.Lock(),
            websocket=websocket,
        )
    finally:
        lease.release()

    assert selected_account is None
    assert selected_upstream is None
    record_error.assert_not_awaited()
    connect_upstream.assert_not_awaited()
    release_reservation.assert_awaited_once_with(None)
    assert websocket_send.await_args is not None
    sent_payload = json.loads(websocket_send.await_args.args[0])
    assert sent_payload["status"] == 429
    assert sent_payload["error"]["code"] == "proxy_overloaded"
    assert request_logs.calls[0]["error_code"] == "proxy_overloaded"


@pytest.mark.asyncio
async def test_connect_proxy_websocket_surfaces_refresh_transport_error(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_ws_refresh_timeout")
    release_reservation = AsyncMock()

    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(side_effect=asyncio.TimeoutError()))
    monkeypatch.setattr(service, "_release_websocket_reservation", release_reservation)

    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_refresh_timeout",
        model="gpt-5.1",
        service_tier="fast",
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
    )

    websocket_send = AsyncMock()
    websocket = cast(WebSocket, SimpleNamespace(send_text=websocket_send))
    selected_account, selected_upstream = await service._connect_proxy_websocket(
        {},
        sticky_key=None,
        sticky_kind=None,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        model="gpt-5.1",
        request_state=request_state,
        api_key=None,
        client_send_lock=anyio.Lock(),
        websocket=websocket,
    )

    assert selected_account is None
    assert selected_upstream is None
    release_reservation.assert_awaited_once_with(None)
    await_args = websocket_send.await_args
    assert await_args is not None
    sent_payload = json.loads(await_args.args[0])
    assert sent_payload["status"] == 502
    assert sent_payload["error"]["code"] == "upstream_unavailable"
    assert sent_payload["error"]["message"] == "Request to upstream timed out"
    assert request_logs.calls[0]["request_id"] == "ws_req_refresh_timeout"
    assert request_logs.calls[0]["error_code"] == "upstream_unavailable"
    assert request_logs.calls[0]["transport"] == "websocket"


@pytest.mark.asyncio
async def test_select_websocket_connect_account_requires_preferred_account_for_previous_response(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_prev_owner_mismatch",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
    )
    selected_account = _make_account("acc_other")
    emit_connect_failure = AsyncMock()

    monkeypatch.setattr(
        service,
        "_select_account_with_budget_compatible",
        AsyncMock(return_value=AccountSelection(account=selected_account, error_message=None)),
    )
    monkeypatch.setattr(service, "_emit_websocket_connect_failure", emit_connect_failure)

    result = await service._select_websocket_connect_account(
        10_000.0,
        sticky_key=None,
        sticky_kind=None,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        model="gpt-5.1",
        request_state=request_state,
        api_key=None,
        client_send_lock=anyio.Lock(),
        websocket=cast(WebSocket, SimpleNamespace()),
        reallocate_sticky=False,
        sticky_max_age_seconds=None,
        exclude_account_ids=set(),
        preferred_account_id="acc_owner",
        require_preferred_account=True,
    )

    assert result is None
    emit_connect_failure.assert_awaited_once()
    call = emit_connect_failure.await_args
    assert call is not None
    assert call.kwargs["status_code"] == 502
    assert call.kwargs["error_code"] == "upstream_unavailable"
    assert call.kwargs["account_id"] == "acc_owner"


@pytest.mark.asyncio
async def test_select_websocket_connect_account_records_fail_closed_for_preferred_account_mismatch(
    monkeypatch,
    caplog,
):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_prev_owner_mismatch_metric",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        previous_response_id="resp_prev_owner",
        preferred_account_id="acc_owner",
        session_id="turn_ws_owner_mismatch",
    )
    selected_account = _make_account("acc_other")
    counter = _ObservedCounter()

    monkeypatch.setattr(proxy_service, "PROMETHEUS_AVAILABLE", True)
    monkeypatch.setattr(proxy_service, "continuity_fail_closed_total", counter, raising=False)
    monkeypatch.setattr(
        service,
        "_select_account_with_budget_compatible",
        AsyncMock(return_value=AccountSelection(account=selected_account, error_message=None)),
    )
    monkeypatch.setattr(service, "_release_websocket_reservation", AsyncMock())

    caplog.set_level(logging.WARNING, logger="app.modules.proxy.service")
    websocket_send = AsyncMock()
    result = await service._select_websocket_connect_account(
        10_000.0,
        sticky_key=None,
        sticky_kind=None,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        model="gpt-5.1",
        request_state=request_state,
        api_key=None,
        client_send_lock=anyio.Lock(),
        websocket=cast(WebSocket, SimpleNamespace(send_text=websocket_send)),
        reallocate_sticky=False,
        sticky_max_age_seconds=None,
        exclude_account_ids=set(),
        preferred_account_id="acc_owner",
        require_preferred_account=True,
    )

    assert result is None
    await_args = websocket_send.await_args
    assert await_args is not None
    sent_payload = json.loads(await_args.args[0])
    assert sent_payload["status"] == 502
    assert sent_payload["error"]["code"] == "upstream_unavailable"
    assert "continuity_fail_closed surface=websocket_connect reason=owner_account_unavailable" in caplog.text
    assert "resp_prev_owner" not in caplog.text
    assert counter.samples == [
        {
            "labels": {"surface": "websocket_connect", "reason": "owner_account_unavailable"},
            "value": 1.0,
        }
    ]


@pytest.mark.asyncio
async def test_select_websocket_connect_account_preferred_owner_missing_fails_closed(
    monkeypatch,
    caplog,
):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_prev_owner_missing",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        previous_response_id="resp_prev_owner",
        preferred_account_id="acc_owner",
        session_id="turn_ws_owner_missing",
    )
    counter = _ObservedCounter()

    monkeypatch.setattr(proxy_service, "PROMETHEUS_AVAILABLE", True)
    monkeypatch.setattr(proxy_service, "continuity_fail_closed_total", counter, raising=False)
    monkeypatch.setattr(
        service,
        "_select_account_with_budget_compatible",
        AsyncMock(
            return_value=AccountSelection(
                account=None,
                error_message="No active accounts available",
                error_code="no_accounts",
            )
        ),
    )
    monkeypatch.setattr(service, "_release_websocket_reservation", AsyncMock())

    caplog.set_level(logging.WARNING, logger="app.modules.proxy.service")
    websocket_send = AsyncMock()
    result = await service._select_websocket_connect_account(
        10_000.0,
        sticky_key=None,
        sticky_kind=None,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        model="gpt-5.1",
        request_state=request_state,
        api_key=None,
        client_send_lock=anyio.Lock(),
        websocket=cast(WebSocket, SimpleNamespace(send_text=websocket_send)),
        reallocate_sticky=False,
        sticky_max_age_seconds=None,
        exclude_account_ids=set(),
        preferred_account_id="acc_owner",
        require_preferred_account=True,
    )

    assert result is None
    await_args = websocket_send.await_args
    assert await_args is not None
    sent_payload = json.loads(await_args.args[0])
    assert sent_payload["status"] == 502
    assert sent_payload["error"]["code"] == "upstream_unavailable"
    assert sent_payload["error"]["message"] == "Previous response owner account is unavailable; retry later."
    assert request_logs.calls[0]["account_id"] == "acc_owner"
    assert request_logs.calls[0]["error_code"] == "upstream_unavailable"
    assert "continuity_fail_closed surface=websocket_connect reason=owner_account_unavailable" in caplog.text
    assert "resp_prev_owner" not in caplog.text
    assert counter.samples == [
        {
            "labels": {"surface": "websocket_connect", "reason": "owner_account_unavailable"},
            "value": 1.0,
        }
    ]


@pytest.mark.asyncio
async def test_connect_proxy_websocket_surfaces_forced_refresh_transport_error(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_ws_forced_refresh_timeout")
    initial_error = proxy_module.ProxyResponseError(401, openai_error("invalid_api_key", "expired"))
    release_reservation = AsyncMock()

    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(side_effect=[account, asyncio.TimeoutError()]))
    monkeypatch.setattr(service, "_open_upstream_websocket", AsyncMock(side_effect=initial_error))
    monkeypatch.setattr(service, "_release_websocket_reservation", release_reservation)

    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_forced_refresh_timeout",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
    )

    websocket_send = AsyncMock()
    websocket = cast(WebSocket, SimpleNamespace(send_text=websocket_send))
    selected_account, selected_upstream = await service._connect_proxy_websocket(
        {},
        sticky_key=None,
        sticky_kind=None,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        model="gpt-5.1",
        request_state=request_state,
        api_key=None,
        client_send_lock=anyio.Lock(),
        websocket=websocket,
    )

    assert selected_account is None
    assert selected_upstream is None
    release_reservation.assert_awaited_once_with(None)
    await_args = websocket_send.await_args
    assert await_args is not None
    sent_payload = json.loads(await_args.args[0])
    assert sent_payload["status"] == 502
    assert sent_payload["error"]["code"] == "upstream_unavailable"
    assert sent_payload["error"]["message"] == "Request to upstream timed out"
    assert request_logs.calls[0]["request_id"] == "ws_req_forced_refresh_timeout"
    assert request_logs.calls[0]["error_code"] == "upstream_unavailable"
    assert request_logs.calls[0]["transport"] == "websocket"


@pytest.mark.asyncio
async def test_connect_proxy_websocket_maps_handshake_budget_exhaustion_to_timeout_error(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_ws_handshake_budget")
    handle_connect_error = AsyncMock()

    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(
        service,
        "_open_upstream_websocket",
        AsyncMock(
            side_effect=proxy_module.ProxyResponseError(
                502,
                openai_error("upstream_unavailable", "Proxy request budget exhausted"),
            )
        ),
    )
    monkeypatch.setattr(service, "_handle_websocket_connect_error", handle_connect_error)
    monkeypatch.setattr(service, "_release_websocket_reservation", AsyncMock())
    monkeypatch.setattr(proxy_service.time, "monotonic", lambda: 100.0)

    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_handshake_budget",
        model="gpt-5.1",
        service_tier="priority",
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=100.0,
    )

    websocket_send = AsyncMock()
    websocket = cast(WebSocket, SimpleNamespace(send_text=websocket_send))
    selected_account, selected_upstream = await service._connect_proxy_websocket(
        {},
        sticky_key=None,
        sticky_kind=None,
        prefer_earlier_reset=False,
        routing_strategy="usage_weighted",
        model="gpt-5.1",
        request_state=request_state,
        api_key=None,
        client_send_lock=anyio.Lock(),
        websocket=websocket,
    )

    assert selected_account is None
    assert selected_upstream is None
    handle_connect_error.assert_not_awaited()
    await_args = websocket_send.await_args
    assert await_args is not None
    sent_payload = json.loads(await_args.args[0])
    assert sent_payload["status"] == 502
    assert sent_payload["error"]["code"] == "upstream_request_timeout"
    assert sent_payload["error"]["message"] == "Proxy request budget exhausted"
    assert request_logs.calls[0]["request_id"] == "ws_req_handshake_budget"
    assert request_logs.calls[0]["error_code"] == "upstream_request_timeout"


@pytest.mark.asyncio
async def test_prepare_websocket_response_create_request_normalizes_payload_and_reserves_forwarded_tier(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    reserve_usage = AsyncMock(return_value=None)
    stale_api_key = ApiKeyData(
        id="key_stale",
        name="stale",
        key_prefix="sk-stale",
        allowed_models=["gpt-5.1"],
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )
    refreshed_api_key = ApiKeyData(
        id="key_stale",
        name="refreshed",
        key_prefix="sk-fresh",
        allowed_models=["gpt-5.2"],
        enforced_model="gpt-5.2",
        enforced_reasoning_effort="high",
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )

    monkeypatch.setattr(service, "_reserve_websocket_api_key_usage", reserve_usage)
    monkeypatch.setattr(
        service,
        "_refresh_websocket_api_key_policy",
        AsyncMock(return_value=refreshed_api_key),
    )

    prepared = await service._prepare_websocket_response_create_request(
        {
            "type": "response.create",
            "model": "gpt-5.1",
            "input": "hello",
            "promptCacheKey": "thread_123",
            "promptCacheRetention": "12h",
            "tools": [{"type": "web_search_preview"}],
            "service_tier": "priority",
            "reasoning": {"effort": "low"},
        },
        headers={"session_id": "sid-ignored"},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=300,
        api_key=stale_api_key,
    )

    reserve_usage.assert_awaited_once_with(
        refreshed_api_key,
        request_model="gpt-5.2",
        request_service_tier="priority",
    )
    assert prepared.request_state.model == "gpt-5.2"
    assert prepared.request_state.service_tier == "priority"
    assert prepared.request_state.reasoning_effort == "high"
    assert prepared.affinity_policy.key == "thread_123"
    assert prepared.affinity_policy.kind == proxy_service.StickySessionKind.PROMPT_CACHE
    normalized_payload = json.loads(prepared.text_data)
    assert normalized_payload["input"] == [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}]
    assert normalized_payload["prompt_cache_key"] == "thread_123"
    assert "promptCacheKey" not in normalized_payload
    assert "promptCacheRetention" not in normalized_payload
    assert "prompt_cache_retention" not in normalized_payload
    assert normalized_payload["tools"] == [{"type": "web_search"}]
    assert normalized_payload["model"] == "gpt-5.2"
    assert normalized_payload["reasoning"] == {"effort": "high"}
    assert normalized_payload["service_tier"] == "priority"


@pytest.mark.asyncio
async def test_prepare_websocket_response_create_request_logs_affinity_metadata(monkeypatch, caplog):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    reserve_usage = AsyncMock(return_value=None)
    api_key = ApiKeyData(
        id="key_ws_shape",
        name="shape",
        key_prefix="sk-shape",
        allowed_models=["gpt-5.1"],
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )

    class Settings:
        log_proxy_request_payload = False
        log_proxy_request_shape = True
        log_proxy_request_shape_raw_cache_key = False
        log_proxy_service_tier_trace = False
        openai_prompt_cache_key_derivation_enabled = True

    monkeypatch.setattr(proxy_service, "get_settings", lambda: Settings())
    monkeypatch.setattr(service, "_reserve_websocket_api_key_usage", reserve_usage)
    monkeypatch.setattr(service, "_refresh_websocket_api_key_policy", AsyncMock(return_value=api_key))

    token = set_request_id("req_ws_shape_1")
    try:
        caplog.set_level(logging.WARNING)
        prepared = await service._prepare_websocket_response_create_request(
            {
                "type": "response.create",
                "model": "gpt-5.1",
                "input": "hello",
            },
            headers={"session_id": "ws-session-1"},
            codex_session_affinity=True,
            openai_cache_affinity=True,
            sticky_threads_enabled=False,
            openai_cache_affinity_max_age_seconds=300,
            api_key=api_key,
        )
    finally:
        reset_request_id(token)

    assert prepared.affinity_policy.kind == proxy_service.StickySessionKind.CODEX_SESSION
    assert "proxy_request_shape" in caplog.text
    assert "kind=websocket" in caplog.text
    assert "sticky_kind=codex_session" in caplog.text
    assert "sticky_key_source=session_header" in caplog.text
    assert "prompt_cache_key_set=True" in caplog.text


@pytest.mark.asyncio
async def test_prepare_websocket_response_create_request_releases_reservation_on_payload_too_large(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    reservation = SimpleNamespace(reservation_id="res_large_ws", model="gpt-5.1")
    reserve_usage = AsyncMock(return_value=reservation)
    release_usage = AsyncMock()
    api_key = ApiKeyData(
        id="key_ws_large",
        name="large",
        key_prefix="sk-large",
        allowed_models=["gpt-5.1"],
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )

    class Settings:
        log_proxy_request_payload = False
        log_proxy_request_shape = False
        log_proxy_request_shape_raw_cache_key = False
        log_proxy_service_tier_trace = False
        openai_prompt_cache_key_derivation_enabled = True

    monkeypatch.setattr(proxy_service, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_service, "_UPSTREAM_RESPONSE_CREATE_WARN_BYTES", 64)
    monkeypatch.setattr(proxy_service, "_UPSTREAM_RESPONSE_CREATE_MAX_BYTES", 128)
    monkeypatch.setattr(service, "_reserve_websocket_api_key_usage", reserve_usage)
    monkeypatch.setattr(service, "_release_websocket_reservation", release_usage)
    monkeypatch.setattr(service, "_refresh_websocket_api_key_policy", AsyncMock(return_value=api_key))

    with pytest.raises(proxy_service.ProxyResponseError) as exc_info:
        await service._prepare_websocket_response_create_request(
            {
                "type": "response.create",
                "model": "gpt-5.1",
                "input": [{"role": "user", "content": [{"type": "input_text", "text": "x" * 256}]}],
            },
            headers={},
            codex_session_affinity=False,
            openai_cache_affinity=True,
            sticky_threads_enabled=False,
            openai_cache_affinity_max_age_seconds=300,
            api_key=api_key,
        )

    assert exc_info.value.status_code == 413
    release_usage.assert_awaited_once_with(reservation)


@pytest.mark.asyncio
async def test_prepare_websocket_response_create_request_does_not_infer_previous_response_id_from_session_scope(
    monkeypatch,
):
    request_logs = _RequestLogsRecorder()
    request_logs.latest_response_by_session[("turn_ws_scope", None)] = "resp_latest_scope"
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    reserve_usage = AsyncMock(return_value=None)
    api_key = ApiKeyData(
        id="key_ws_no_session_infer",
        name="ws-no-infer",
        key_prefix="sk-ws-no-infer",
        allowed_models=["gpt-5.1"],
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )

    class Settings:
        log_proxy_request_payload = False
        log_proxy_request_shape = False
        log_proxy_request_shape_raw_cache_key = False
        log_proxy_service_tier_trace = False
        openai_prompt_cache_key_derivation_enabled = True

    monkeypatch.setattr(proxy_service, "get_settings", lambda: Settings())
    monkeypatch.setattr(service, "_reserve_websocket_api_key_usage", reserve_usage)
    monkeypatch.setattr(service, "_refresh_websocket_api_key_policy", AsyncMock(return_value=api_key))

    prepared = await service._prepare_websocket_response_create_request(
        {
            "type": "response.create",
            "model": "gpt-5.1",
            "input": "hello",
        },
        headers={"session_id": "turn_ws_scope", "x-codex-turn-state": "turn_ws_scope"},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=300,
        api_key=api_key,
    )

    assert prepared.request_state.previous_response_id is None
    assert request_logs.session_lookup_calls == []


def test_slim_response_create_payload_rewrites_top_level_historical_input_image():
    payload: dict[str, JsonValue] = {
        "type": "response.create",
        "model": "gpt-5.1",
        "input": [
            {"type": "input_image", "image_url": "data:image/png;base64," + ("A" * 1500)},
            {"role": "user", "content": [{"type": "input_text", "text": "ping"}]},
        ],
    }

    slimmed_payload, summary = proxy_service._slim_response_create_payload_for_upstream(payload, max_bytes=256)
    slimmed_input = cast(list[JsonValue], slimmed_payload["input"])

    assert summary is not None
    assert summary["historical_images_slimmed"] == 1
    assert slimmed_input[0] == {
        "role": "user",
        "content": [{"type": "input_text", "text": proxy_service._RESPONSE_CREATE_IMAGE_OMISSION_NOTICE}],
    }
    assert slimmed_input[-1] == {"role": "user", "content": [{"type": "input_text", "text": "ping"}]}


def test_slim_response_create_preserves_all_items_when_no_user_message():
    payload: dict[str, JsonValue] = {
        "type": "response.create",
        "model": "gpt-5.1",
        "input": [
            {"type": "function_call_output", "call_id": "call_1", "output": "A" * 2000},
            {"type": "function_call_output", "call_id": "call_2", "output": "B" * 2000},
        ],
    }

    slimmed_payload, summary = proxy_service._slim_response_create_payload_for_upstream(payload, max_bytes=256)

    slimmed_input = cast(list[JsonValue], slimmed_payload["input"])
    assert len(slimmed_input) == 2
    first = slimmed_input[0]
    second = slimmed_input[1]
    assert isinstance(first, dict) and first["call_id"] == "call_1"
    assert isinstance(second, dict) and second["call_id"] == "call_2"
    assert summary is None


def test_slim_response_create_handles_object_valued_content_image():
    payload: dict[str, JsonValue] = {
        "type": "response.create",
        "model": "gpt-5.1",
        "input": [
            {
                "role": "user",
                "content": {"type": "input_image", "image_url": "data:image/png;base64," + ("A" * 1500)},
            },
            {"role": "user", "content": [{"type": "input_text", "text": "describe this"}]},
        ],
    }

    slimmed_payload, summary = proxy_service._slim_response_create_payload_for_upstream(payload, max_bytes=4096)
    slimmed_input = cast(list[JsonValue], slimmed_payload["input"])

    assert isinstance(summary, dict)
    assert summary["historical_images_slimmed"] == 1
    assert len(slimmed_input) == 2
    first_item = slimmed_input[0]
    assert isinstance(first_item, dict)
    first_content = first_item["content"]
    assert isinstance(first_content, dict)
    assert first_content["type"] == "input_text"


def test_websocket_receive_timeout_prefers_idle_timeout_when_budget_allows(monkeypatch):
    monkeypatch.setattr(proxy_service.time, "monotonic", lambda: 100.0)

    timeout = proxy_service._websocket_receive_timeout_for_pending_requests(
        [90.0, 95.0],
        proxy_request_budget_seconds=20.0,
        stream_idle_timeout_seconds=5.0,
    )

    assert timeout is not None
    assert timeout.timeout_seconds == 5.0
    assert timeout.error_code == "stream_idle_timeout"
    assert timeout.error_message == "Upstream stream idle timeout"


def test_websocket_receive_timeout_prefers_request_budget_when_sooner(monkeypatch):
    monkeypatch.setattr(proxy_service.time, "monotonic", lambda: 100.0)

    timeout = proxy_service._websocket_receive_timeout_for_pending_requests(
        [90.0],
        proxy_request_budget_seconds=11.0,
        stream_idle_timeout_seconds=5.0,
    )

    assert timeout is not None
    assert timeout.timeout_seconds == 1.0
    assert timeout.error_code == "upstream_request_timeout"
    assert timeout.error_message == "Proxy request budget exhausted"


@pytest.mark.asyncio
async def test_fail_expired_pending_websocket_requests_keeps_newer_requests(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    emit_terminal_error = AsyncMock()
    release_reservation = AsyncMock()

    monkeypatch.setattr(service, "_emit_websocket_terminal_error", emit_terminal_error)
    monkeypatch.setattr(service, "_release_websocket_reservation", release_reservation)
    monkeypatch.setattr(proxy_service.time, "monotonic", lambda: 100.0)

    expired_request = proxy_service._WebSocketRequestState(
        request_id="ws_req_expired",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=90.0,
        response_id="resp_expired",
    )
    newer_request = proxy_service._WebSocketRequestState(
        request_id="ws_req_newer",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=99.5,
        response_id="resp_newer",
    )
    pending_requests = deque([expired_request, newer_request])

    await service._fail_expired_pending_websocket_requests(
        account_id_value="acc_ws_budget",
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        request_budget_seconds=5.0,
        error_code="upstream_request_timeout",
        error_message="Proxy request budget exhausted",
        api_key=None,
        websocket=cast(WebSocket, SimpleNamespace()),
        client_send_lock=anyio.Lock(),
    )

    assert list(pending_requests) == [newer_request]
    emit_terminal_error.assert_awaited_once()
    release_reservation.assert_awaited_once_with(None)
    assert len(request_logs.calls) == 1
    assert request_logs.calls[0]["request_id"] == "resp_expired"
    assert request_logs.calls[0]["error_code"] == "upstream_request_timeout"


@pytest.mark.asyncio
async def test_finalize_websocket_request_state_updates_balancer_state(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_ws_finalize")
    record_success = AsyncMock()
    handle_stream_error = AsyncMock()

    monkeypatch.setattr(service._load_balancer, "record_success", record_success)
    monkeypatch.setattr(service, "_handle_stream_error", handle_stream_error)
    monkeypatch.setattr(service, "_settle_stream_api_key_usage", AsyncMock(return_value=True))

    completed_payload: dict[str, JsonValue] = {
        "type": "response.completed",
        "response": {
            "id": "resp_ws_complete",
            "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
        },
    }
    completed_event = parse_sse_event(f"data: {json.dumps(completed_payload)}\n\n")
    assert completed_event is not None
    completed_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_complete",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
    )
    completed_upstream_control = proxy_service._WebSocketUpstreamControl()

    await service._finalize_websocket_request_state(
        completed_state,
        account=account,
        account_id_value=account.id,
        event=completed_event,
        event_type="response.completed",
        payload=completed_payload,
        api_key=None,
        upstream_control=completed_upstream_control,
        response_create_gate=asyncio.Semaphore(1),
    )

    record_success.assert_awaited_once_with(account)
    handle_stream_error.assert_not_awaited()
    assert completed_upstream_control.reconnect_requested is False

    failed_payload: dict[str, JsonValue] = {
        "type": "response.failed",
        "response": {
            "id": "resp_ws_failed",
            "error": {"code": "rate_limit_exceeded", "message": "slow down"},
            "usage": {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
        },
    }
    failed_event = parse_sse_event(f"data: {json.dumps(failed_payload)}\n\n")
    assert failed_event is not None
    failed_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_failed",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
    )
    failed_upstream_control = proxy_service._WebSocketUpstreamControl()

    await service._finalize_websocket_request_state(
        failed_state,
        account=account,
        account_id_value=account.id,
        event=failed_event,
        event_type="response.failed",
        payload=failed_payload,
        api_key=None,
        upstream_control=failed_upstream_control,
        response_create_gate=asyncio.Semaphore(1),
    )

    handle_args = handle_stream_error.await_args
    assert handle_args is not None
    assert handle_args.args[0] == account
    assert handle_args.args[2] == "rate_limit_exceeded"
    assert failed_upstream_control.reconnect_requested is True

    record_success.reset_mock()
    handle_stream_error.reset_mock()
    incomplete_payload: dict[str, JsonValue] = {
        "type": "response.incomplete",
        "response": {
            "id": "resp_ws_incomplete",
            "status": "incomplete",
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        },
    }
    incomplete_event = parse_sse_event(f"data: {json.dumps(incomplete_payload)}\n\n")
    assert incomplete_event is not None
    incomplete_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_incomplete",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
    )
    incomplete_upstream_control = proxy_service._WebSocketUpstreamControl()

    await service._finalize_websocket_request_state(
        incomplete_state,
        account=account,
        account_id_value=account.id,
        event=incomplete_event,
        event_type="response.incomplete",
        payload=incomplete_payload,
        api_key=None,
        upstream_control=incomplete_upstream_control,
        response_create_gate=asyncio.Semaphore(1),
    )

    record_success.assert_not_awaited()
    handle_stream_error.assert_not_awaited()
    assert incomplete_upstream_control.reconnect_requested is False
    assert request_logs.calls[-1]["status"] == "error"


@pytest.mark.asyncio
async def test_process_upstream_websocket_text_does_not_match_foreign_response_id_to_only_pending_request(
    monkeypatch,
):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    finalize_request_state = AsyncMock()
    account = _make_account("acc_ws_pending")

    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize_request_state)

    pending_request = proxy_service._WebSocketRequestState(
        request_id="ws_req_pending",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        response_id="resp_ws_a",
    )
    pending_requests = deque([pending_request])
    payload = {
        "type": "response.completed",
        "response": {
            "id": "resp_ws_b",
            "usage": {"input_tokens": 7, "output_tokens": 11, "total_tokens": 18},
        },
    }

    await service._process_upstream_websocket_text(
        json.dumps(payload, separators=(",", ":")),
        account=account,
        account_id_value=account.id,
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        api_key=None,
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        response_create_gate=asyncio.Semaphore(1),
    )

    finalize_request_state.assert_not_awaited()
    assert list(pending_requests) == [pending_request]


@pytest.mark.asyncio
async def test_process_upstream_websocket_text_does_not_match_foreign_completed_event_to_only_unresolved_request(
    monkeypatch,
):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    finalize_request_state = AsyncMock()
    account = _make_account("acc_ws_pending_precreated")

    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize_request_state)

    pending_request = proxy_service._WebSocketRequestState(
        request_id="ws_req_pending_precreated",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        awaiting_response_created=True,
        request_text=json.dumps(
            {
                "type": "response.create",
                "model": "gpt-5.1",
                "input": [{"role": "user", "content": [{"type": "input_text", "text": "first"}]}],
            },
            separators=(",", ":"),
        ),
    )
    pending_requests = deque([pending_request])
    payload = {
        "type": "response.completed",
        "response": {
            "id": "resp_ws_foreign_completed",
            "usage": {"input_tokens": 7, "output_tokens": 11, "total_tokens": 18},
        },
    }

    downstream_text = await service._process_upstream_websocket_text(
        json.dumps(payload, separators=(",", ":")),
        account=account,
        account_id_value=account.id,
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        api_key=None,
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        response_create_gate=asyncio.Semaphore(1),
    )

    assert downstream_text == json.dumps(payload, separators=(",", ":"))
    finalize_request_state.assert_not_awaited()
    assert list(pending_requests) == [pending_request]


@pytest.mark.parametrize(
    "payload",
    [
        {
            "type": "error",
            "status": 400,
            "error": {
                "type": "invalid_request_error",
                "code": "previous_response_not_found",
                "message": "Cannot continue conversation because upstream lost resp_anchor_a.",
                "param": "previous_response_id",
            },
            "response": {"id": "resp_ws_foreign_prev_nf"},
        },
        {
            "type": "response.failed",
            "response": {
                "id": "resp_ws_foreign_prev_nf",
                "status": "failed",
                "error": {
                    "type": "invalid_request_error",
                    "code": "previous_response_not_found",
                    "message": "Cannot continue conversation because upstream lost resp_anchor_a.",
                    "param": "previous_response_id",
                },
            },
        },
    ],
)
@pytest.mark.asyncio
async def test_process_upstream_websocket_text_skips_foreign_prev_nf_for_mismatched_created_followup(
    monkeypatch,
    payload,
):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    finalize_request_state = AsyncMock()
    handle_stream_error = AsyncMock()
    account = _make_account("acc_ws_foreign_prev_nf_created_mismatch")

    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize_request_state)
    monkeypatch.setattr(service, "_handle_stream_error", handle_stream_error)

    pending_request = proxy_service._WebSocketRequestState(
        request_id="ws_req_followup_created_prev_nf_mismatch",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        response_id="resp_ws_followup_created_b",
        previous_response_id="resp_anchor_b",
    )
    pending_requests = deque([pending_request])
    upstream_control = proxy_service._WebSocketUpstreamControl()

    downstream_text = await service._process_upstream_websocket_text(
        json.dumps(payload, separators=(",", ":")),
        account=account,
        account_id_value=account.id,
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        api_key=None,
        upstream_control=upstream_control,
        response_create_gate=asyncio.Semaphore(1),
    )

    assert downstream_text == json.dumps(payload, separators=(",", ":"))
    finalize_request_state.assert_not_awaited()
    handle_stream_error.assert_not_awaited()
    assert upstream_control.reconnect_requested is False
    assert list(pending_requests) == [pending_request]


@pytest.mark.parametrize(
    "payload",
    [
        {
            "type": "error",
            "status": 400,
            "error": {
                "type": "invalid_request_error",
                "code": "previous_response_not_found",
                "message": "Previous response with id 'resp_anchor' not found.",
                "param": "previous_response_id",
            },
            "response": {"id": "resp_ws_foreign_prev_nf"},
        },
        {
            "type": "response.failed",
            "response": {
                "id": "resp_ws_foreign_prev_nf",
                "status": "failed",
                "error": {
                    "type": "invalid_request_error",
                    "code": "previous_response_not_found",
                    "message": "Previous response with id 'resp_anchor' not found.",
                    "param": "previous_response_id",
                },
            },
        },
    ],
)
@pytest.mark.asyncio
async def test_process_upstream_websocket_text_masks_foreign_previous_response_not_found_for_only_created_followup(
    monkeypatch,
    payload,
):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    finalize_request_state = AsyncMock()
    handle_stream_error = AsyncMock()
    account = _make_account("acc_ws_foreign_prev_nf_created")

    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize_request_state)
    monkeypatch.setattr(service, "_handle_stream_error", handle_stream_error)

    pending_request = proxy_service._WebSocketRequestState(
        request_id="ws_req_followup_created_prev_nf",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        response_id="resp_ws_followup_created",
        previous_response_id="resp_anchor",
    )
    pending_requests = deque([pending_request])
    upstream_control = proxy_service._WebSocketUpstreamControl()

    downstream_text = await service._process_upstream_websocket_text(
        json.dumps(payload, separators=(",", ":")),
        account=account,
        account_id_value=account.id,
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        api_key=None,
        upstream_control=upstream_control,
        response_create_gate=asyncio.Semaphore(1),
    )

    assert '"type":"response.failed"' in downstream_text
    assert '"code":"stream_incomplete"' in downstream_text
    assert "previous_response_not_found" not in downstream_text
    finalize_request_state.assert_awaited_once()
    finalize_call = finalize_request_state.await_args
    assert finalize_call is not None
    assert finalize_call.args[0] is pending_request
    assert finalize_call.kwargs["event_type"] == "response.failed"
    handle_stream_error.assert_not_awaited()
    assert upstream_control.reconnect_requested is True
    assert upstream_control.suppress_downstream_event is False
    assert list(pending_requests) == []


@pytest.mark.parametrize(
    "payload",
    [
        {
            "type": "error",
            "status": 400,
            "error": {
                "type": "invalid_request_error",
                "code": "previous_response_not_found",
                "message": "Previous response with id 'resp_anchor_a' not found.",
                "param": "previous_response_id",
            },
            "response": {"id": "resp_ws_foreign_prev_nf"},
        },
        {
            "type": "response.failed",
            "response": {
                "id": "resp_ws_foreign_prev_nf",
                "status": "failed",
                "error": {
                    "type": "invalid_request_error",
                    "code": "previous_response_not_found",
                    "message": "Previous response with id 'resp_anchor_a' not found.",
                    "param": "previous_response_id",
                },
            },
        },
    ],
)
@pytest.mark.asyncio
async def test_process_upstream_websocket_text_matches_foreign_prev_nf_to_anchor_with_two_followups(
    monkeypatch,
    payload,
):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    finalize_request_state = AsyncMock()
    handle_stream_error = AsyncMock()
    account = _make_account("acc_ws_foreign_prev_nf_multiple_followups")

    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize_request_state)
    monkeypatch.setattr(service, "_handle_stream_error", handle_stream_error)

    followup_request_a = proxy_service._WebSocketRequestState(
        request_id="ws_req_followup_created_prev_nf_a",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        response_id="resp_ws_followup_created_a",
        previous_response_id="resp_anchor_a",
    )
    followup_request_b = proxy_service._WebSocketRequestState(
        request_id="ws_req_followup_created_prev_nf_b",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        response_id="resp_ws_followup_created_b",
        previous_response_id="resp_anchor_b",
    )
    pending_requests = deque([followup_request_a, followup_request_b])
    upstream_control = proxy_service._WebSocketUpstreamControl()

    downstream_text = await service._process_upstream_websocket_text(
        json.dumps(payload, separators=(",", ":")),
        account=account,
        account_id_value=account.id,
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        api_key=None,
        upstream_control=upstream_control,
        response_create_gate=asyncio.Semaphore(1),
    )

    assert '"type":"response.failed"' in downstream_text
    assert '"code":"stream_incomplete"' in downstream_text
    assert '"id":"resp_ws_followup_created_a"' in downstream_text
    assert "previous_response_not_found" not in downstream_text
    finalize_request_state.assert_awaited_once()
    finalize_call = finalize_request_state.await_args
    assert finalize_call is not None
    assert finalize_call.args[0] is followup_request_a
    handle_stream_error.assert_not_awaited()
    assert upstream_control.reconnect_requested is True
    assert list(pending_requests) == [followup_request_b]


@pytest.mark.parametrize(
    "payload",
    [
        {
            "type": "error",
            "status": 400,
            "error": {
                "type": "invalid_request_error",
                "code": "previous_response_not_found",
                "message": "Cannot continue conversation because upstream lost resp_anchor_1234.",
                "param": "previous_response_id",
            },
            "response": {"id": "resp_ws_foreign_prev_nf"},
        },
        {
            "type": "response.failed",
            "response": {
                "id": "resp_ws_foreign_prev_nf",
                "status": "failed",
                "error": {
                    "type": "invalid_request_error",
                    "code": "previous_response_not_found",
                    "message": "Cannot continue conversation because upstream lost resp_anchor_1234.",
                    "param": "previous_response_id",
                },
            },
        },
    ],
)
@pytest.mark.asyncio
async def test_process_upstream_websocket_text_matches_foreign_prev_nf_with_overlapping_anchors(
    monkeypatch,
    payload,
):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    finalize_request_state = AsyncMock()
    handle_stream_error = AsyncMock()
    account = _make_account("acc_ws_foreign_prev_nf_overlap_followups")

    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize_request_state)
    monkeypatch.setattr(service, "_handle_stream_error", handle_stream_error)

    followup_request_a = proxy_service._WebSocketRequestState(
        request_id="ws_req_followup_overlap_a",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        response_id="resp_ws_followup_overlap_a",
        previous_response_id="resp_anchor_123",
    )
    followup_request_b = proxy_service._WebSocketRequestState(
        request_id="ws_req_followup_overlap_b",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        response_id="resp_ws_followup_overlap_b",
        previous_response_id="resp_anchor_1234",
    )
    pending_requests = deque([followup_request_a, followup_request_b])
    upstream_control = proxy_service._WebSocketUpstreamControl()

    downstream_text = await service._process_upstream_websocket_text(
        json.dumps(payload, separators=(",", ":")),
        account=account,
        account_id_value=account.id,
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        api_key=None,
        upstream_control=upstream_control,
        response_create_gate=asyncio.Semaphore(1),
    )

    assert '"type":"response.failed"' in downstream_text
    assert '"code":"stream_incomplete"' in downstream_text
    assert '"id":"resp_ws_followup_overlap_b"' in downstream_text
    assert "previous_response_not_found" not in downstream_text
    finalize_request_state.assert_awaited_once()
    finalize_call = finalize_request_state.await_args
    assert finalize_call is not None
    assert finalize_call.args[0] is followup_request_b
    handle_stream_error.assert_not_awaited()
    assert upstream_control.reconnect_requested is True
    assert list(pending_requests) == [followup_request_a]


@pytest.mark.parametrize(
    "payload",
    [
        {
            "type": "error",
            "status": 400,
            "error": {
                "type": "invalid_request_error",
                "code": "previous_response_not_found",
                "message": "Cannot continue conversation because upstream lost resp_anchor_a.",
                "param": "previous_response_id",
            },
        },
        {
            "type": "response.failed",
            "response": {
                "status": "failed",
                "error": {
                    "type": "invalid_request_error",
                    "code": "previous_response_not_found",
                    "message": "Cannot continue conversation because upstream lost resp_anchor_a.",
                    "param": "previous_response_id",
                },
            },
        },
    ],
)
@pytest.mark.asyncio
async def test_process_upstream_websocket_text_skips_anonymous_prev_nf_for_mismatched_created_followup(
    monkeypatch,
    payload,
):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    finalize_request_state = AsyncMock()
    handle_stream_error = AsyncMock()
    account = _make_account("acc_ws_anonymous_prev_nf_created_followup_mismatch")

    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize_request_state)
    monkeypatch.setattr(service, "_handle_stream_error", handle_stream_error)

    followup_request = proxy_service._WebSocketRequestState(
        request_id="ws_req_followup_created_anonymous_prev_nf_mismatch",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        response_id="resp_ws_followup_created_b",
        previous_response_id="resp_anchor_b",
    )
    pending_requests = deque([followup_request])
    upstream_control = proxy_service._WebSocketUpstreamControl()

    downstream_text = await service._process_upstream_websocket_text(
        json.dumps(payload, separators=(",", ":")),
        account=account,
        account_id_value=account.id,
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        api_key=None,
        upstream_control=upstream_control,
        response_create_gate=asyncio.Semaphore(1),
    )

    assert downstream_text == json.dumps(payload, separators=(",", ":"))
    finalize_request_state.assert_not_awaited()
    handle_stream_error.assert_not_awaited()
    assert upstream_control.reconnect_requested is False
    assert list(pending_requests) == [followup_request]


@pytest.mark.parametrize(
    "payload",
    [
        {
            "type": "error",
            "status": 400,
            "error": {
                "type": "invalid_request_error",
                "code": "previous_response_not_found",
                "message": "Previous response with id 'resp_anchor_a' not found.",
                "param": "previous_response_id",
            },
        },
        {
            "type": "response.failed",
            "response": {
                "status": "failed",
                "error": {
                    "type": "invalid_request_error",
                    "code": "previous_response_not_found",
                    "message": "Previous response with id 'resp_anchor_a' not found.",
                    "param": "previous_response_id",
                },
            },
        },
    ],
)
@pytest.mark.asyncio
async def test_process_upstream_websocket_text_matches_anonymous_prev_nf_to_anchor_with_two_followups(
    monkeypatch,
    payload,
):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    finalize_request_state = AsyncMock()
    handle_stream_error = AsyncMock()
    account = _make_account("acc_ws_anonymous_prev_nf_multiple_followups")

    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize_request_state)
    monkeypatch.setattr(service, "_handle_stream_error", handle_stream_error)

    followup_request_a = proxy_service._WebSocketRequestState(
        request_id="ws_req_followup_created_anonymous_prev_nf_a",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        response_id="resp_ws_followup_created_a",
        previous_response_id="resp_anchor_a",
    )
    followup_request_b = proxy_service._WebSocketRequestState(
        request_id="ws_req_followup_created_anonymous_prev_nf_b",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        response_id="resp_ws_followup_created_b",
        previous_response_id="resp_anchor_b",
    )
    pending_requests = deque([followup_request_a, followup_request_b])
    upstream_control = proxy_service._WebSocketUpstreamControl()

    downstream_text = await service._process_upstream_websocket_text(
        json.dumps(payload, separators=(",", ":")),
        account=account,
        account_id_value=account.id,
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        api_key=None,
        upstream_control=upstream_control,
        response_create_gate=asyncio.Semaphore(1),
    )

    assert '"type":"response.failed"' in downstream_text
    assert '"code":"stream_incomplete"' in downstream_text
    assert '"id":"resp_ws_followup_created_a"' in downstream_text
    assert "previous_response_not_found" not in downstream_text
    finalize_request_state.assert_awaited_once()
    finalize_call = finalize_request_state.await_args
    assert finalize_call is not None
    assert finalize_call.args[0] is followup_request_a
    handle_stream_error.assert_not_awaited()
    assert upstream_control.reconnect_requested is True
    assert list(pending_requests) == [followup_request_b]


@pytest.mark.parametrize(
    "payload",
    [
        {
            "type": "error",
            "status": 400,
            "error": {
                "type": "invalid_request_error",
                "code": "previous_response_not_found",
                "message": "Cannot continue conversation because upstream lost resp_anchor_1234.",
                "param": "previous_response_id",
            },
        },
        {
            "type": "response.failed",
            "response": {
                "status": "failed",
                "error": {
                    "type": "invalid_request_error",
                    "code": "previous_response_not_found",
                    "message": "Cannot continue conversation because upstream lost resp_anchor_1234.",
                    "param": "previous_response_id",
                },
            },
        },
    ],
)
@pytest.mark.asyncio
async def test_process_upstream_websocket_text_matches_anonymous_prev_nf_with_overlapping_anchors(
    monkeypatch,
    payload,
):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    finalize_request_state = AsyncMock()
    handle_stream_error = AsyncMock()
    account = _make_account("acc_ws_anonymous_prev_nf_overlap_followups")

    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize_request_state)
    monkeypatch.setattr(service, "_handle_stream_error", handle_stream_error)

    followup_request_a = proxy_service._WebSocketRequestState(
        request_id="ws_req_followup_overlap_anonymous_a",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        response_id="resp_ws_followup_overlap_anonymous_a",
        previous_response_id="resp_anchor_123",
    )
    followup_request_b = proxy_service._WebSocketRequestState(
        request_id="ws_req_followup_overlap_anonymous_b",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        response_id="resp_ws_followup_overlap_anonymous_b",
        previous_response_id="resp_anchor_1234",
    )
    pending_requests = deque([followup_request_a, followup_request_b])
    upstream_control = proxy_service._WebSocketUpstreamControl()

    downstream_text = await service._process_upstream_websocket_text(
        json.dumps(payload, separators=(",", ":")),
        account=account,
        account_id_value=account.id,
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        api_key=None,
        upstream_control=upstream_control,
        response_create_gate=asyncio.Semaphore(1),
    )

    assert '"type":"response.failed"' in downstream_text
    assert '"code":"stream_incomplete"' in downstream_text
    assert '"id":"resp_ws_followup_overlap_anonymous_b"' in downstream_text
    assert "previous_response_not_found" not in downstream_text
    finalize_request_state.assert_awaited_once()
    finalize_call = finalize_request_state.await_args
    assert finalize_call is not None
    assert finalize_call.args[0] is followup_request_b
    handle_stream_error.assert_not_awaited()
    assert upstream_control.reconnect_requested is True
    assert list(pending_requests) == [followup_request_a]


@pytest.mark.parametrize(
    "payload",
    [
        {
            "type": "error",
            "status": 400,
            "error": {
                "type": "invalid_request_error",
                "code": "previous_response_not_found",
                "message": "Previous response with id 'resp_anchor' not found.",
                "param": "previous_response_id",
            },
        },
        {
            "type": "response.failed",
            "response": {
                "status": "failed",
                "error": {
                    "type": "invalid_request_error",
                    "code": "previous_response_not_found",
                    "message": "Previous response with id 'resp_anchor' not found.",
                    "param": "previous_response_id",
                },
            },
        },
    ],
)
@pytest.mark.asyncio
async def test_process_upstream_websocket_text_masks_anonymous_previous_response_not_found_for_created_followup(
    monkeypatch,
    payload,
):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    finalize_request_state = AsyncMock()
    handle_stream_error = AsyncMock()
    account = _make_account("acc_ws_anonymous_prev_nf_created_followup")

    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize_request_state)
    monkeypatch.setattr(service, "_handle_stream_error", handle_stream_error)

    inflight_request = proxy_service._WebSocketRequestState(
        request_id="ws_req_inflight_created_followup_prev_nf",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        response_id="resp_ws_inflight",
    )
    followup_request = proxy_service._WebSocketRequestState(
        request_id="ws_req_followup_created_anonymous_prev_nf",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        response_id="resp_ws_followup_created",
        previous_response_id="resp_anchor",
    )
    pending_requests = deque([inflight_request, followup_request])
    upstream_control = proxy_service._WebSocketUpstreamControl()

    downstream_text = await service._process_upstream_websocket_text(
        json.dumps(payload, separators=(",", ":")),
        account=account,
        account_id_value=account.id,
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        api_key=None,
        upstream_control=upstream_control,
        response_create_gate=asyncio.Semaphore(1),
    )

    assert '"type":"response.failed"' in downstream_text
    assert '"code":"stream_incomplete"' in downstream_text
    assert '"id":"resp_ws_followup_created"' in downstream_text
    assert "previous_response_not_found" not in downstream_text
    finalize_request_state.assert_awaited_once()
    finalize_call = finalize_request_state.await_args
    assert finalize_call is not None
    assert finalize_call.args[0] is followup_request
    assert finalize_call.kwargs["event_type"] == "response.failed"
    handle_stream_error.assert_not_awaited()
    assert upstream_control.reconnect_requested is True
    assert upstream_control.suppress_downstream_event is False
    assert list(pending_requests) == [inflight_request]


@pytest.mark.parametrize(
    "payload",
    [
        {
            "type": "error",
            "status": 400,
            "error": {
                "type": "invalid_request_error",
                "code": "previous_response_not_found",
                "message": "Previous response with id 'resp_anchor' not found.",
                "param": "previous_response_id",
            },
        },
        {
            "type": "response.failed",
            "response": {
                "status": "failed",
                "error": {
                    "type": "invalid_request_error",
                    "code": "previous_response_not_found",
                    "message": "Previous response with id 'resp_anchor' not found.",
                    "param": "previous_response_id",
                },
            },
        },
    ],
)
@pytest.mark.asyncio
async def test_process_upstream_websocket_text_masks_anonymous_previous_response_not_found_for_same_anchor_followups(
    monkeypatch,
    payload,
):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    finalize_request_state = AsyncMock()
    handle_stream_error = AsyncMock()
    account = _make_account("acc_ws_anonymous_prev_nf_same_anchor")

    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize_request_state)
    monkeypatch.setattr(service, "_handle_stream_error", handle_stream_error)

    followup_request_a = proxy_service._WebSocketRequestState(
        request_id="ws_req_followup_same_anchor_a",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        previous_response_id="resp_anchor",
        request_text='{"type":"response.create","previous_response_id":"resp_anchor"}',
    )
    followup_request_b = proxy_service._WebSocketRequestState(
        request_id="ws_req_followup_same_anchor_b",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        previous_response_id="resp_anchor",
        request_text='{"type":"response.create","previous_response_id":"resp_anchor"}',
    )
    pending_requests = deque([followup_request_a, followup_request_b])
    upstream_control = proxy_service._WebSocketUpstreamControl()

    downstream_text = await service._process_upstream_websocket_text(
        json.dumps(payload, separators=(",", ":")),
        account=account,
        account_id_value=account.id,
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        api_key=None,
        upstream_control=upstream_control,
        response_create_gate=asyncio.Semaphore(2),
    )

    assert "previous_response_not_found" not in downstream_text
    assert upstream_control.suppress_downstream_event is True
    assert upstream_control.reconnect_requested is True
    assert upstream_control.downstream_texts is not None
    assert len(upstream_control.downstream_texts) == 2
    for emitted_text in upstream_control.downstream_texts:
        assert '"type":"response.failed"' in emitted_text
        assert '"code":"stream_incomplete"' in emitted_text
        assert "previous_response_not_found" not in emitted_text
    assert finalize_request_state.await_count == 2
    finalized_requests = [call.args[0] for call in finalize_request_state.await_args_list]
    assert finalized_requests == [followup_request_a, followup_request_b]
    for call in finalize_request_state.await_args_list:
        assert call.kwargs["event_type"] == "response.failed"
    handle_stream_error.assert_not_awaited()
    assert list(pending_requests) == []


@pytest.mark.asyncio
async def test_process_upstream_websocket_text_transparently_retries_precreated_usage_limit_failure(
    monkeypatch,
):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    finalize_request_state = AsyncMock()
    handle_stream_error = AsyncMock()
    account = _make_account("acc_ws_precreated_retry")

    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize_request_state)
    monkeypatch.setattr(service, "_handle_stream_error", handle_stream_error)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.1",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "retry me"}]}],
    }
    pending_request = proxy_service._WebSocketRequestState(
        request_id="ws_req_precreated_retry",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        awaiting_response_created=True,
        request_text=json.dumps(request_payload, separators=(",", ":")),
    )
    pending_requests = deque([pending_request])
    upstream_control = proxy_service._WebSocketUpstreamControl()
    upstream_payload = {
        "type": "response.failed",
        "response": {
            "id": "resp_ws_precreated_fail",
            "status": "failed",
            "error": {"code": "usage_limit_reached", "message": "usage limit reached"},
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        },
    }
    upstream_text = json.dumps(upstream_payload, separators=(",", ":"))

    downstream_text = await service._process_upstream_websocket_text(
        upstream_text,
        account=account,
        account_id_value=account.id,
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        api_key=None,
        upstream_control=upstream_control,
        response_create_gate=asyncio.Semaphore(1),
    )

    assert downstream_text == upstream_text
    finalize_request_state.assert_not_awaited()
    handle_stream_error.assert_awaited_once()
    handle_call = handle_stream_error.await_args
    assert handle_call is not None
    assert handle_call.args[0] == account
    assert handle_call.args[2] == "usage_limit_reached"
    assert upstream_control.reconnect_requested is True
    assert upstream_control.suppress_downstream_event is True
    assert upstream_control.replay_request_state is pending_request
    assert pending_request.replay_count == 1
    assert list(pending_requests) == []


@pytest.mark.asyncio
async def test_process_upstream_websocket_text_transparently_retries_precreated_usage_limit_error_event(
    monkeypatch,
):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    finalize_request_state = AsyncMock()
    handle_stream_error = AsyncMock()
    account = _make_account("acc_ws_precreated_retry_error_event")

    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize_request_state)
    monkeypatch.setattr(service, "_handle_stream_error", handle_stream_error)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.1",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "retry me"}]}],
    }
    pending_request = proxy_service._WebSocketRequestState(
        request_id="ws_req_precreated_retry_error_event",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        awaiting_response_created=True,
        request_text=json.dumps(request_payload, separators=(",", ":")),
    )
    pending_requests = deque([pending_request])
    upstream_control = proxy_service._WebSocketUpstreamControl()
    upstream_payload = {
        "type": "error",
        "status": 429,
        "error": {
            "type": "invalid_request_error",
            "code": "usage_limit_reached",
            "message": "The usage limit has been reached",
        },
    }
    upstream_text = json.dumps(upstream_payload, separators=(",", ":"))

    downstream_text = await service._process_upstream_websocket_text(
        upstream_text,
        account=account,
        account_id_value=account.id,
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        api_key=None,
        upstream_control=upstream_control,
        response_create_gate=asyncio.Semaphore(1),
    )

    assert downstream_text == upstream_text
    finalize_request_state.assert_not_awaited()
    handle_stream_error.assert_awaited_once()
    handle_call = handle_stream_error.await_args
    assert handle_call is not None
    assert handle_call.args[0] == account
    assert handle_call.args[2] == "usage_limit_reached"
    assert upstream_control.reconnect_requested is True
    assert upstream_control.suppress_downstream_event is True
    assert upstream_control.replay_request_state is pending_request
    assert pending_request.replay_count == 1
    assert list(pending_requests) == []


@pytest.mark.asyncio
async def test_process_upstream_websocket_text_maps_previous_response_usage_limit_to_upstream_unavailable(
    monkeypatch,
):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    finalize_request_state = AsyncMock()
    handle_stream_error = AsyncMock()
    account = _make_account("acc_ws_prev_quota_owner")

    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize_request_state)
    monkeypatch.setattr(service, "_handle_stream_error", handle_stream_error)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.1",
        "instructions": "",
        "previous_response_id": "resp_anchor",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "continue"}]}],
    }
    pending_request = proxy_service._WebSocketRequestState(
        request_id="ws_req_prev_quota_unavailable",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        awaiting_response_created=True,
        request_text=json.dumps(request_payload, separators=(",", ":")),
        previous_response_id="resp_anchor",
        preferred_account_id=account.id,
    )
    pending_requests = deque([pending_request])
    upstream_control = proxy_service._WebSocketUpstreamControl()
    upstream_payload = {
        "type": "error",
        "status": 429,
        "error": {
            "type": "invalid_request_error",
            "code": "usage_limit_reached",
            "message": "The usage limit has been reached",
        },
    }
    upstream_text = json.dumps(upstream_payload, separators=(",", ":"))

    downstream_text = await service._process_upstream_websocket_text(
        upstream_text,
        account=account,
        account_id_value=account.id,
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        api_key=None,
        upstream_control=upstream_control,
        response_create_gate=asyncio.Semaphore(1),
    )

    assert '"code":"upstream_unavailable"' in downstream_text
    handle_stream_error.assert_awaited_once()
    handle_call = handle_stream_error.await_args
    assert handle_call is not None
    assert handle_call.args[0] == account
    assert handle_call.args[2] == "usage_limit_reached"
    finalize_request_state.assert_awaited_once()
    finalize_call = finalize_request_state.await_args
    assert finalize_call is not None
    assert finalize_call.kwargs["event_type"] == "response.failed"
    payload = finalize_call.kwargs["payload"]
    assert isinstance(payload, dict)
    response_payload = cast(dict[str, JsonValue], payload["response"])
    error_payload = cast(dict[str, JsonValue], response_payload["error"])
    assert error_payload["code"] == "upstream_unavailable"
    assert error_payload["message"] == "Previous response owner account is unavailable; retry later."
    assert upstream_control.reconnect_requested is False
    assert upstream_control.suppress_downstream_event is False
    assert upstream_control.replay_request_state is None
    assert pending_request.replay_count == 0
    assert list(pending_requests) == []


@pytest.mark.asyncio
async def test_proxy_responses_websocket_transparent_replay_preserves_sticky_thread_affinity(
    monkeypatch,
):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    handled_error_codes: list[str] = []
    connect_calls: list[dict[str, object]] = []
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    settings.sticky_threads_enabled = True
    settings.stream_idle_timeout_seconds = 300.0
    settings.proxy_downstream_websocket_idle_timeout_seconds = 120.0

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)

    class _FakeDownstreamWebSocket:
        def __init__(self, request_text: str) -> None:
            self._request_text = request_text
            self._request_sent = False
            self._disconnect_sent = False
            self._done = asyncio.Event()
            self.sent_text: list[str] = []
            self.closed = False

        async def receive(self) -> dict[str, object]:
            if not self._request_sent:
                self._request_sent = True
                return {"type": "websocket.receive", "text": self._request_text}
            if not self._disconnect_sent:
                await self._done.wait()
                self._disconnect_sent = True
                return {"type": "websocket.disconnect"}
            await asyncio.sleep(0)
            return {"type": "websocket.disconnect"}

        async def send_text(self, text: str) -> None:
            self.sent_text.append(text)
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = {}
            if payload.get("type") in {"response.completed", "response.failed", "error"}:
                self._done.set()

        async def send_bytes(self, _data: bytes) -> None:
            return None

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            del code, reason
            self.closed = True
            self._done.set()

    class _FakeUpstreamWebSocket:
        def __init__(self, messages: list[SimpleNamespace]) -> None:
            self.sent_text: list[str] = []
            self.closed = False
            self._messages: asyncio.Queue[SimpleNamespace] = asyncio.Queue()
            for message in messages:
                self._messages.put_nowait(message)

        async def send_text(self, text: str) -> None:
            self.sent_text.append(text)

        async def send_bytes(self, _data: bytes) -> None:
            return None

        async def receive(self) -> SimpleNamespace:
            return await self._messages.get()

        async def close(self) -> None:
            self.closed = True

    first_upstream = _FakeUpstreamWebSocket(
        [
            SimpleNamespace(
                kind="text",
                text=json.dumps(
                    {
                        "type": "response.failed",
                        "response": {
                            "id": "resp_ws_sticky_retry_fail",
                            "status": "failed",
                            "error": {"code": "usage_limit_reached", "message": "usage limit reached"},
                            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                        },
                    },
                    separators=(",", ":"),
                ),
                data=None,
                close_code=None,
                error=None,
            )
        ]
    )
    second_upstream = _FakeUpstreamWebSocket(
        [
            SimpleNamespace(
                kind="text",
                text=json.dumps(
                    {
                        "type": "response.created",
                        "response": {"id": "resp_ws_sticky_retry_ok", "status": "in_progress"},
                    },
                    separators=(",", ":"),
                ),
                data=None,
                close_code=None,
                error=None,
            ),
            SimpleNamespace(
                kind="text",
                text=json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_ws_sticky_retry_ok",
                            "status": "completed",
                            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                        },
                    },
                    separators=(",", ":"),
                ),
                data=None,
                close_code=None,
                error=None,
            ),
        ]
    )

    async def fake_connect_proxy_websocket(
        self,
        headers,
        *,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset,
        routing_strategy,
        model,
        request_state,
        api_key,
        client_send_lock,
        websocket,
    ):
        del (
            self,
            headers,
            sticky_max_age_seconds,
            prefer_earlier_reset,
            routing_strategy,
            request_state,
            api_key,
            client_send_lock,
            websocket,
        )
        connect_calls.append(
            {
                "sticky_key": sticky_key,
                "sticky_kind": sticky_kind,
                "reallocate_sticky": reallocate_sticky,
                "model": model,
            }
        )
        if len(connect_calls) == 1:
            return _make_account("acc_ws_sticky_1"), first_upstream
        return _make_account("acc_ws_sticky_2"), second_upstream

    async def fake_handle_stream_error(self, account, error, code):
        del self, account, error
        handled_error_codes.append(code)

    monkeypatch.setattr(proxy_service.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_service.ProxyService, "_handle_stream_error", fake_handle_stream_error)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.1",
        "instructions": "",
        "prompt_cache_key": "sticky-thread-xyz",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "retry me"}]}],
        "stream": True,
    }
    downstream = _FakeDownstreamWebSocket(json.dumps(request_payload, separators=(",", ":")))

    await service.proxy_responses_websocket(
        cast(WebSocket, downstream),
        {},
        codex_session_affinity=False,
        openai_cache_affinity=False,
        api_key=None,
    )

    emitted_events = [json.loads(event) for event in downstream.sent_text]
    assert [event["type"] for event in emitted_events] == ["response.created", "response.completed"]
    assert handled_error_codes == ["usage_limit_reached"]
    assert len(connect_calls) == 2
    assert connect_calls[0]["sticky_key"] == "sticky-thread-xyz"
    assert connect_calls[0]["sticky_kind"] == proxy_service.StickySessionKind.STICKY_THREAD
    assert connect_calls[0]["reallocate_sticky"] is True
    assert connect_calls[1]["sticky_key"] == "sticky-thread-xyz"
    assert connect_calls[1]["sticky_kind"] == proxy_service.StickySessionKind.STICKY_THREAD
    assert connect_calls[1]["reallocate_sticky"] is True
    assert first_upstream.closed is True
    assert len(first_upstream.sent_text) == 1
    assert len(second_upstream.sent_text) == 1
    assert json.loads(first_upstream.sent_text[0]) == json.loads(second_upstream.sent_text[0])


@pytest.mark.asyncio
async def test_proxy_responses_websocket_replays_precreated_request_after_upstream_close_race(
    monkeypatch,
):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    connect_calls: list[dict[str, object]] = []
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    settings.stream_idle_timeout_seconds = 300.0
    settings.proxy_downstream_websocket_idle_timeout_seconds = 120.0

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)

    class _FakeDownstreamWebSocket:
        def __init__(self, first_request_text: str, second_request_text: str) -> None:
            self._first_request_text = first_request_text
            self._second_request_text = second_request_text
            self._step = 0
            self._first_completed = asyncio.Event()
            self._done = asyncio.Event()
            self.sent_text: list[str] = []

        async def receive(self) -> dict[str, object]:
            if self._step == 0:
                self._step = 1
                return {"type": "websocket.receive", "text": self._first_request_text}
            if self._step == 1:
                await self._first_completed.wait()
                self._step = 2
                return {"type": "websocket.receive", "text": self._second_request_text}
            if self._step == 2:
                await self._done.wait()
                self._step = 3
                return {"type": "websocket.disconnect"}
            await asyncio.sleep(0)
            return {"type": "websocket.disconnect"}

        async def send_text(self, text: str) -> None:
            self.sent_text.append(text)
            payload = json.loads(text)
            if payload.get("type") == "response.completed":
                response_payload = payload.get("response") or {}
                if response_payload.get("id") == "resp_ws_race_first":
                    self._first_completed.set()
                if response_payload.get("id") == "resp_ws_race_second":
                    self._done.set()
            if payload.get("type") in {"response.failed", "error"}:
                self._done.set()

        async def send_bytes(self, _data: bytes) -> None:
            return None

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            del code, reason
            self._done.set()

    class _RaceUpstreamWebSocket:
        def __init__(self, messages: list[SimpleNamespace], *, close_delay_seconds: float = 0.0) -> None:
            self.sent_text: list[str] = []
            self.closed = False
            self._messages: asyncio.Queue[SimpleNamespace] = asyncio.Queue()
            for message in messages:
                self._messages.put_nowait(message)
            self._close_delay_seconds = close_delay_seconds

        async def send_text(self, text: str) -> None:
            self.sent_text.append(text)

        async def send_bytes(self, _data: bytes) -> None:
            return None

        async def receive(self) -> SimpleNamespace:
            message = await self._messages.get()
            if message.kind == "close" and self._close_delay_seconds > 0:
                await asyncio.sleep(self._close_delay_seconds)
            return message

        async def close(self) -> None:
            self.closed = True

    first_upstream = _RaceUpstreamWebSocket(
        [
            SimpleNamespace(
                kind="text",
                text=json.dumps(
                    {
                        "type": "response.created",
                        "response": {"id": "resp_ws_race_first", "status": "in_progress"},
                    },
                    separators=(",", ":"),
                ),
                data=None,
                close_code=None,
                error=None,
            ),
            SimpleNamespace(
                kind="text",
                text=json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_ws_race_first",
                            "status": "completed",
                            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                        },
                    },
                    separators=(",", ":"),
                ),
                data=None,
                close_code=None,
                error=None,
            ),
            SimpleNamespace(kind="close", text=None, data=None, close_code=1001, error=None),
        ],
        close_delay_seconds=0.05,
    )
    second_upstream = _RaceUpstreamWebSocket(
        [
            SimpleNamespace(
                kind="text",
                text=json.dumps(
                    {
                        "type": "response.created",
                        "response": {"id": "resp_ws_race_second", "status": "in_progress"},
                    },
                    separators=(",", ":"),
                ),
                data=None,
                close_code=None,
                error=None,
            ),
            SimpleNamespace(
                kind="text",
                text=json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_ws_race_second",
                            "status": "completed",
                            "usage": {"input_tokens": 2, "output_tokens": 2, "total_tokens": 4},
                        },
                    },
                    separators=(",", ":"),
                ),
                data=None,
                close_code=None,
                error=None,
            ),
        ]
    )

    async def fake_connect_proxy_websocket(
        self,
        headers,
        *,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset,
        routing_strategy,
        model,
        request_state,
        api_key,
        client_send_lock,
        websocket,
    ):
        del (
            self,
            headers,
            sticky_key,
            sticky_kind,
            sticky_max_age_seconds,
            prefer_earlier_reset,
            routing_strategy,
            request_state,
            api_key,
            client_send_lock,
            websocket,
        )
        connect_calls.append({"model": model, "reallocate_sticky": reallocate_sticky})
        if len(connect_calls) == 1:
            return _make_account("acc_ws_race_1"), first_upstream
        return _make_account("acc_ws_race_2"), second_upstream

    monkeypatch.setattr(proxy_service.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    first_request = {
        "type": "response.create",
        "model": "gpt-5.4-mini",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "first"}]}],
        "stream": True,
    }
    second_request = {
        "type": "response.create",
        "model": "gpt-5.4-mini",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "second"}]}],
        "stream": True,
    }
    downstream = _FakeDownstreamWebSocket(
        json.dumps(first_request, separators=(",", ":")),
        json.dumps(second_request, separators=(",", ":")),
    )

    await service.proxy_responses_websocket(
        cast(WebSocket, downstream),
        {"x-codex-turn-state": "turn_race_ws"},
        codex_session_affinity=True,
        openai_cache_affinity=True,
        api_key=None,
    )

    emitted_events = [json.loads(event) for event in downstream.sent_text]
    assert [event["type"] for event in emitted_events] == [
        "response.created",
        "response.completed",
        "response.created",
        "response.completed",
    ]
    assert [event["response"]["id"] for event in emitted_events if "response" in event] == [
        "resp_ws_race_first",
        "resp_ws_race_first",
        "resp_ws_race_second",
        "resp_ws_race_second",
    ]
    assert len(connect_calls) == 2
    assert connect_calls[0]["reallocate_sticky"] is False
    assert connect_calls[1]["reallocate_sticky"] is False
    assert len(second_upstream.sent_text) == 1
    assert len(first_upstream.sent_text) >= 1
    assert json.loads(first_upstream.sent_text[-1]) == json.loads(second_upstream.sent_text[0])


@pytest.mark.asyncio
async def test_proxy_responses_websocket_prefers_previous_response_owner_from_request_logs(monkeypatch):
    request_logs = _RequestLogsRecorder()
    request_logs.response_owner_by_id[("resp_prev_owner", None, "sid_owner")] = "acc_owner_prev"
    service = proxy_service.ProxyService(_repo_factory(request_logs))

    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    settings.stream_idle_timeout_seconds = 300.0
    settings.proxy_downstream_websocket_idle_timeout_seconds = 120.0
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)

    class _FakeDownstreamWebSocket:
        def __init__(self, request_text: str) -> None:
            self._request_text = request_text
            self._request_sent = False
            self._disconnect_sent = False
            self._done = asyncio.Event()
            self.sent_text: list[str] = []

        async def receive(self) -> dict[str, object]:
            if not self._request_sent:
                self._request_sent = True
                return {"type": "websocket.receive", "text": self._request_text}
            if not self._disconnect_sent:
                await self._done.wait()
                self._disconnect_sent = True
                return {"type": "websocket.disconnect"}
            await asyncio.sleep(0)
            return {"type": "websocket.disconnect"}

        async def send_text(self, text: str) -> None:
            self.sent_text.append(text)
            payload = json.loads(text)
            if payload.get("type") in {"response.completed", "response.failed", "error"}:
                self._done.set()

        async def send_bytes(self, _data: bytes) -> None:
            return None

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            del code, reason
            self._done.set()

    class _FakeUpstreamWebSocket:
        def __init__(self, messages: list[SimpleNamespace]) -> None:
            self.sent_text: list[str] = []
            self.closed = False
            self._messages: asyncio.Queue[SimpleNamespace] = asyncio.Queue()
            for message in messages:
                self._messages.put_nowait(message)

        async def send_text(self, text: str) -> None:
            self.sent_text.append(text)

        async def send_bytes(self, _data: bytes) -> None:
            return None

        async def receive(self) -> SimpleNamespace:
            return await self._messages.get()

        async def close(self) -> None:
            self.closed = True

    upstream = _FakeUpstreamWebSocket(
        [
            SimpleNamespace(
                kind="text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_owner_retry", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
                data=None,
                close_code=None,
                error=None,
            ),
            SimpleNamespace(
                kind="text",
                text=json.dumps(
                    {"type": "response.completed", "response": {"id": "resp_owner_retry", "status": "completed"}},
                    separators=(",", ":"),
                ),
                data=None,
                close_code=None,
                error=None,
            ),
        ]
    )
    captured_preferred_accounts: list[str | None] = []

    async def fake_connect_proxy_websocket(
        self,
        headers,
        *,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset,
        routing_strategy,
        model,
        request_state,
        api_key,
        client_send_lock,
        websocket,
    ):
        del (
            self,
            headers,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset,
            routing_strategy,
            model,
            api_key,
            client_send_lock,
            websocket,
        )
        captured_preferred_accounts.append(request_state.preferred_account_id)
        return _make_account("acc_selected_any"), upstream

    monkeypatch.setattr(proxy_service.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.1",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "continue"}]}],
        "previous_response_id": "resp_prev_owner",
        "stream": True,
    }
    downstream = _FakeDownstreamWebSocket(json.dumps(request_payload, separators=(",", ":")))

    await service.proxy_responses_websocket(
        cast(WebSocket, downstream),
        {"session_id": "sid_owner"},
        codex_session_affinity=False,
        openai_cache_affinity=False,
        api_key=None,
    )

    assert captured_preferred_accounts == ["acc_owner_prev"]
    assert request_logs.lookup_calls == [("resp_prev_owner", None, "sid_owner")]
    emitted_events = [json.loads(event) for event in downstream.sent_text]
    assert [event["type"] for event in emitted_events] == ["response.created", "response.completed"]


@pytest.mark.asyncio
async def test_proxy_responses_websocket_uses_turn_state_as_owner_lookup_session_scope(monkeypatch):
    request_logs = _RequestLogsRecorder()
    request_logs.response_owner_by_id[("resp_prev_owner", None, "turn_scope_owner")] = "acc_owner_prev"
    service = proxy_service.ProxyService(_repo_factory(request_logs))

    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    settings.stream_idle_timeout_seconds = 300.0
    settings.proxy_downstream_websocket_idle_timeout_seconds = 120.0
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)

    class _FakeDownstreamWebSocket:
        def __init__(self, request_text: str) -> None:
            self._request_text = request_text
            self._request_sent = False
            self._disconnect_sent = False
            self._done = asyncio.Event()
            self.sent_text: list[str] = []

        async def receive(self) -> dict[str, object]:
            if not self._request_sent:
                self._request_sent = True
                return {"type": "websocket.receive", "text": self._request_text}
            if not self._disconnect_sent:
                await self._done.wait()
                self._disconnect_sent = True
                return {"type": "websocket.disconnect"}
            await asyncio.sleep(0)
            return {"type": "websocket.disconnect"}

        async def send_text(self, text: str) -> None:
            self.sent_text.append(text)
            payload = json.loads(text)
            if payload.get("type") in {"response.completed", "response.failed", "error"}:
                self._done.set()

        async def send_bytes(self, _data: bytes) -> None:
            return None

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            del code, reason
            self._done.set()

    class _FakeUpstreamWebSocket:
        def __init__(self, messages: list[SimpleNamespace]) -> None:
            self.sent_text: list[str] = []
            self.closed = False
            self._messages: asyncio.Queue[SimpleNamespace] = asyncio.Queue()
            for message in messages:
                self._messages.put_nowait(message)

        async def send_text(self, text: str) -> None:
            self.sent_text.append(text)

        async def send_bytes(self, _data: bytes) -> None:
            return None

        async def receive(self) -> SimpleNamespace:
            return await self._messages.get()

        async def close(self) -> None:
            self.closed = True

    upstream = _FakeUpstreamWebSocket(
        [
            SimpleNamespace(
                kind="text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_owner_retry", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
                data=None,
                close_code=None,
                error=None,
            ),
            SimpleNamespace(
                kind="text",
                text=json.dumps(
                    {"type": "response.completed", "response": {"id": "resp_owner_retry", "status": "completed"}},
                    separators=(",", ":"),
                ),
                data=None,
                close_code=None,
                error=None,
            ),
        ]
    )
    captured_preferred_accounts: list[str | None] = []

    async def fake_connect_proxy_websocket(
        self,
        headers,
        *,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset,
        routing_strategy,
        model,
        request_state,
        api_key,
        client_send_lock,
        websocket,
    ):
        del (
            self,
            headers,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset,
            routing_strategy,
            model,
            api_key,
            client_send_lock,
            websocket,
        )
        captured_preferred_accounts.append(request_state.preferred_account_id)
        return _make_account("acc_selected_any"), upstream

    monkeypatch.setattr(proxy_service.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.1",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "continue"}]}],
        "previous_response_id": "resp_prev_owner",
        "stream": True,
    }
    downstream = _FakeDownstreamWebSocket(json.dumps(request_payload, separators=(",", ":")))

    await service.proxy_responses_websocket(
        cast(WebSocket, downstream),
        {"x-codex-turn-state": "turn_scope_owner"},
        codex_session_affinity=False,
        openai_cache_affinity=False,
        api_key=None,
    )

    assert captured_preferred_accounts == ["acc_owner_prev"]
    assert request_logs.lookup_calls == [("resp_prev_owner", None, "turn_scope_owner")]
    emitted_events = [json.loads(event) for event in downstream.sent_text]
    assert [event["type"] for event in emitted_events] == ["response.created", "response.completed"]


@pytest.mark.asyncio
async def test_proxy_responses_websocket_prefers_turn_state_over_session_for_owner_lookup_scope(monkeypatch):
    request_logs = _RequestLogsRecorder()
    request_logs.response_owner_by_id[("resp_prev_owner", None, "turn_scope_owner")] = "acc_owner_prev"
    service = proxy_service.ProxyService(_repo_factory(request_logs))

    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    settings.stream_idle_timeout_seconds = 300.0
    settings.proxy_downstream_websocket_idle_timeout_seconds = 120.0
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)

    class _FakeDownstreamWebSocket:
        def __init__(self, request_text: str) -> None:
            self._request_text = request_text
            self._request_sent = False
            self._disconnect_sent = False
            self._done = asyncio.Event()
            self.sent_text: list[str] = []

        async def receive(self) -> dict[str, object]:
            if not self._request_sent:
                self._request_sent = True
                return {"type": "websocket.receive", "text": self._request_text}
            if not self._disconnect_sent:
                await self._done.wait()
                self._disconnect_sent = True
                return {"type": "websocket.disconnect"}
            await asyncio.sleep(0)
            return {"type": "websocket.disconnect"}

        async def send_text(self, text: str) -> None:
            self.sent_text.append(text)
            payload = json.loads(text)
            if payload.get("type") in {"response.completed", "response.failed", "error"}:
                self._done.set()

        async def send_bytes(self, _data: bytes) -> None:
            return None

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            del code, reason
            self._done.set()

    class _FakeUpstreamWebSocket:
        def __init__(self, messages: list[SimpleNamespace]) -> None:
            self.sent_text: list[str] = []
            self.closed = False
            self._messages: asyncio.Queue[SimpleNamespace] = asyncio.Queue()
            for message in messages:
                self._messages.put_nowait(message)

        async def send_text(self, text: str) -> None:
            self.sent_text.append(text)

        async def send_bytes(self, _data: bytes) -> None:
            return None

        async def receive(self) -> SimpleNamespace:
            return await self._messages.get()

        async def close(self) -> None:
            self.closed = True

    upstream = _FakeUpstreamWebSocket(
        [
            SimpleNamespace(
                kind="text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_owner_retry", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
                data=None,
                close_code=None,
                error=None,
            ),
            SimpleNamespace(
                kind="text",
                text=json.dumps(
                    {"type": "response.completed", "response": {"id": "resp_owner_retry", "status": "completed"}},
                    separators=(",", ":"),
                ),
                data=None,
                close_code=None,
                error=None,
            ),
        ]
    )
    captured_preferred_accounts: list[str | None] = []

    async def fake_connect_proxy_websocket(
        self,
        headers,
        *,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset,
        routing_strategy,
        model,
        request_state,
        api_key,
        client_send_lock,
        websocket,
    ):
        del (
            self,
            headers,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset,
            routing_strategy,
            model,
            api_key,
            client_send_lock,
            websocket,
        )
        captured_preferred_accounts.append(request_state.preferred_account_id)
        return _make_account("acc_selected_any"), upstream

    monkeypatch.setattr(proxy_service.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.1",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "continue"}]}],
        "previous_response_id": "resp_prev_owner",
        "stream": True,
    }
    downstream = _FakeDownstreamWebSocket(json.dumps(request_payload, separators=(",", ":")))

    await service.proxy_responses_websocket(
        cast(WebSocket, downstream),
        {"session_id": "shared_session_owner", "x-codex-turn-state": "turn_scope_owner"},
        codex_session_affinity=False,
        openai_cache_affinity=False,
        api_key=None,
    )

    assert captured_preferred_accounts == ["acc_owner_prev"]
    assert request_logs.lookup_calls == [("resp_prev_owner", None, "turn_scope_owner")]
    emitted_events = [json.loads(event) for event in downstream.sent_text]
    assert [event["type"] for event in emitted_events] == ["response.created", "response.completed"]


@pytest.mark.asyncio
async def test_proxy_responses_websocket_previous_response_owner_lookup_failure_returns_upstream_unavailable(
    monkeypatch,
):
    request_logs = _RequestLogsRecorder()
    request_logs.lookup_error = RuntimeError("lookup unavailable")
    service = proxy_service.ProxyService(_repo_factory(request_logs))

    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    settings.stream_idle_timeout_seconds = 300.0
    settings.proxy_downstream_websocket_idle_timeout_seconds = 120.0
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)

    class _FakeDownstreamWebSocket:
        def __init__(self, request_text: str) -> None:
            self._request_text = request_text
            self._request_sent = False
            self._disconnect_sent = False
            self._done = asyncio.Event()
            self.sent_text: list[str] = []

        async def receive(self) -> dict[str, object]:
            if not self._request_sent:
                self._request_sent = True
                return {"type": "websocket.receive", "text": self._request_text}
            if not self._disconnect_sent:
                await self._done.wait()
                self._disconnect_sent = True
                return {"type": "websocket.disconnect"}
            await asyncio.sleep(0)
            return {"type": "websocket.disconnect"}

        async def send_text(self, text: str) -> None:
            self.sent_text.append(text)
            self._done.set()

        async def send_bytes(self, _data: bytes) -> None:
            return None

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            del code, reason
            self._done.set()

    async def fail_connect_proxy_websocket(*args, **kwargs):
        del args, kwargs
        raise AssertionError("owner lookup failure must fail before websocket connect")

    monkeypatch.setattr(proxy_service.ProxyService, "_connect_proxy_websocket", fail_connect_proxy_websocket)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.1",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "continue"}]}],
        "previous_response_id": "resp_prev_lookup_failure",
        "stream": True,
    }
    downstream = _FakeDownstreamWebSocket(json.dumps(request_payload, separators=(",", ":")))

    await service.proxy_responses_websocket(
        cast(WebSocket, downstream),
        {"session_id": "sid_owner_lookup_failure"},
        codex_session_affinity=False,
        openai_cache_affinity=False,
        api_key=None,
    )

    assert request_logs.lookup_calls == [("resp_prev_lookup_failure", None, "sid_owner_lookup_failure")]
    assert len(downstream.sent_text) == 1
    payload = json.loads(downstream.sent_text[0])
    assert payload["type"] == "response.failed"
    assert payload["response"]["status"] == "failed"
    assert payload["response"]["error"]["code"] == "upstream_unavailable"
    assert payload["response"]["error"]["message"] == "Previous response owner lookup failed; retry later."


@pytest.mark.asyncio
async def test_stream_with_retry_releases_api_key_reservation_when_owner_lookup_fails(monkeypatch):
    request_logs = _RequestLogsRecorder()
    get_usage_reservation_mock = AsyncMock(return_value=SimpleNamespace(status="reserved", items=[]))
    transition_usage_reservation_status_mock = AsyncMock(return_value=True)
    settle_usage_reservation_mock = AsyncMock()
    commit_mock = AsyncMock()
    api_keys_repo = cast(
        ApiKeysRepository,
        SimpleNamespace(
            get_usage_reservation=get_usage_reservation_mock,
            transition_usage_reservation_status=transition_usage_reservation_status_mock,
            settle_usage_reservation=settle_usage_reservation_mock,
            commit=commit_mock,
        ),
    )

    class _RepoContextWithApiKeys:
        def __init__(self) -> None:
            self._repos = ProxyRepositories(
                accounts=cast(AccountsRepository, AsyncMock()),
                usage=cast(UsageRepository, AsyncMock()),
                request_logs=cast(RequestLogsRepository, request_logs),
                sticky_sessions=cast(StickySessionsRepository, AsyncMock()),
                api_keys=api_keys_repo,
                additional_usage=cast(AdditionalUsageRepository, AsyncMock()),
            )

        async def __aenter__(self) -> ProxyRepositories:
            return self._repos

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    service = proxy_service.ProxyService(lambda: _RepoContextWithApiKeys())
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)

    api_key = ApiKeyData(
        id="key_owner_lookup_fail_release",
        name="owner-lookup-fail-release",
        key_prefix="sk-clb-owner",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )
    reservation = proxy_service.ApiKeyUsageReservationData(
        reservation_id="resv_owner_lookup_fail",
        key_id=api_key.id,
        model="gpt-5.4",
    )
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "continue",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "continue"}]}],
            "previous_response_id": "resp_owner_lookup_fail",
            "stream": True,
        }
    )

    owner_lookup_error = proxy_module.ProxyResponseError(
        503,
        openai_error(
            "upstream_unavailable",
            "Previous response owner lookup failed; retry later.",
            error_type="server_error",
        ),
    )
    owner_lookup = AsyncMock(side_effect=owner_lookup_error)
    monkeypatch.setattr(service, "_resolve_websocket_previous_response_owner", owner_lookup)
    select_account = AsyncMock(side_effect=AssertionError("owner lookup failure must happen before account selection"))
    monkeypatch.setattr(service, "_select_account_with_budget_compatible", select_account)

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        async for _ in service._stream_with_retry(
            payload,
            {"x-codex-turn-state": "turn_owner_lookup_fail"},
            codex_session_affinity=False,
            propagate_http_errors=False,
            openai_cache_affinity=False,
            api_key=api_key,
            api_key_reservation=reservation,
            suppress_text_done_events=False,
            request_transport="http",
        ):
            pass

    assert _proxy_error_code(exc_info.value) == "upstream_unavailable"
    owner_lookup.assert_awaited_once()
    select_account.assert_not_called()
    get_usage_reservation_mock.assert_awaited_once_with(reservation.reservation_id)
    transition_usage_reservation_status_mock.assert_awaited_once_with(
        reservation.reservation_id,
        expected_status="reserved",
        new_status="released",
    )
    settle_usage_reservation_mock.assert_awaited_once()
    commit_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_websocket_previous_response_owner_rechecks_same_scope_after_initial_miss(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    clock = {"value": 100.0}
    monkeypatch.setattr(proxy_service.time, "monotonic", lambda: clock["value"])

    owner_1 = await service._resolve_websocket_previous_response_owner(
        previous_response_id="resp_prev_missing",
        api_key=None,
        session_id="req_scope_1",
        surface="websocket",
    )
    clock["value"] = 102.0
    owner_2 = await service._resolve_websocket_previous_response_owner(
        previous_response_id="resp_prev_missing",
        api_key=None,
        session_id="req_scope_1",
        surface="websocket",
    )
    request_logs.response_owner_by_id[("resp_prev_missing", None, None)] = "acc_owner_after_commit"
    clock["value"] = 103.0
    owner_3 = await service._resolve_websocket_previous_response_owner(
        previous_response_id="resp_prev_missing",
        api_key=None,
        session_id="req_scope_1",
        surface="websocket",
    )

    assert owner_1 is None
    assert owner_2 is None
    assert owner_3 == "acc_owner_after_commit"
    assert request_logs.lookup_calls == [
        ("resp_prev_missing", None, "req_scope_1"),
        ("resp_prev_missing", None, "req_scope_1"),
        ("resp_prev_missing", None, "req_scope_1"),
    ]


@pytest.mark.asyncio
async def test_resolve_websocket_previous_response_owner_miss_does_not_evict_known_owner(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    clock = {"value": 100.0}
    monkeypatch.setattr(proxy_service.time, "monotonic", lambda: clock["value"])
    api_key = ApiKeyData(
        id="key_shared",
        name="shared-key",
        key_prefix="sk-shared",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )

    service._remember_websocket_previous_response_owner(
        previous_response_id="resp_prev_shared",
        api_key_id=api_key.id,
        account_id="acc_owner",
    )
    service._remember_websocket_previous_response_owner_miss(
        previous_response_id="resp_prev_shared",
        api_key_id=api_key.id,
        request_cache_scope="req_terminal_b",
    )

    owner = await service._resolve_websocket_previous_response_owner(
        previous_response_id="resp_prev_shared",
        api_key=api_key,
        session_id="req_terminal_a",
        surface="websocket",
    )

    assert owner == "acc_owner"
    assert request_logs.lookup_calls == [("resp_prev_shared", api_key.id, "req_terminal_a")]


@pytest.mark.asyncio
async def test_resolve_websocket_previous_response_owner_prefers_scoped_lookup_over_generic_cache() -> None:
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    api_key = ApiKeyData(
        id="key_shared",
        name="shared-key",
        key_prefix="sk-shared",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )
    service._remember_websocket_previous_response_owner(
        previous_response_id="resp_prev_shared",
        api_key_id=api_key.id,
        account_id="acc_owner_generic",
    )
    request_logs.response_owner_by_id[("resp_prev_shared", api_key.id, "turn_scope_a")] = "acc_owner_scoped"

    owner = await service._resolve_websocket_previous_response_owner(
        previous_response_id="resp_prev_shared",
        api_key=api_key,
        session_id="turn_scope_a",
        surface="websocket",
    )

    assert owner == "acc_owner_scoped"
    assert request_logs.lookup_calls == [("resp_prev_shared", api_key.id, "turn_scope_a")]
    assert service._websocket_previous_response_account_index[("resp_prev_shared", api_key.id, "turn_scope_a")] == (
        "acc_owner_scoped"
    )


@pytest.mark.asyncio
async def test_resolve_websocket_previous_response_owner_uses_unique_scoped_cache_fallback_on_lookup_failure() -> None:
    request_logs = _RequestLogsRecorder()
    request_logs.lookup_error = RuntimeError("request log lookup unavailable")
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    api_key = ApiKeyData(
        id="key_shared",
        name="shared-key",
        key_prefix="sk-shared",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )

    service._remember_websocket_previous_response_owner(
        previous_response_id="resp_prev_shared",
        api_key_id=api_key.id,
        account_id="acc_owner_scoped",
        session_id="turn_scope_a",
    )

    owner = await service._resolve_websocket_previous_response_owner(
        previous_response_id="resp_prev_shared",
        api_key=api_key,
        session_id="turn_scope_b",
        surface="websocket",
    )

    assert owner == "acc_owner_scoped"
    assert request_logs.lookup_calls == [("resp_prev_shared", api_key.id, "turn_scope_b")]


def test_remember_websocket_previous_response_owner_eviction_keeps_latest_entries():
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    limit = proxy_service._WEBSOCKET_PREVIOUS_RESPONSE_ACCOUNT_CACHE_LIMIT

    for index in range(limit + 1):
        service._remember_websocket_previous_response_owner(
            previous_response_id=f"resp_prev_{index}",
            api_key_id="key_1",
            account_id=f"acc_{index}",
        )

    assert len(service._websocket_previous_response_account_index) == limit
    assert ("resp_prev_0", "key_1", None) not in service._websocket_previous_response_account_index
    assert ("resp_prev_1", "key_1", None) in service._websocket_previous_response_account_index
    assert ("resp_prev_4096", "key_1", None) in service._websocket_previous_response_account_index


@pytest.mark.asyncio
async def test_process_upstream_websocket_text_retries_precreated_previous_response_not_found(monkeypatch):
    """A precreated retry-safe full-resend turn (with both
    ``fresh_upstream_request_is_retry_safe=True`` and
    ``fresh_upstream_request_text`` populated by the request-prep path) must
    be transparently retried on a fresh upstream when upstream returns
    ``previous_response_not_found``. The retry strips the stale
    ``previous_response_id`` and replays the prepared fresh text.

    This is the inverse of
    ``test_process_upstream_websocket_text_short_previous_response_not_found_fails_closed``
    below: when the request prep path has classified the turn as retry-safe,
    the masking path must replay rather than fail closed.
    """

    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    finalize_request_state = AsyncMock()
    handle_stream_error = AsyncMock()
    account = _make_account("acc_ws_prev_not_found_retry")

    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize_request_state)
    monkeypatch.setattr(service, "_handle_stream_error", handle_stream_error)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.1",
        "instructions": "",
        "previous_response_id": "resp_anchor",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "continue"}]}],
    }
    fresh_request_payload = dict(request_payload)
    fresh_request_payload.pop("previous_response_id")
    pending_request = proxy_service._WebSocketRequestState(
        request_id="ws_req_prev_not_found_retry",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        awaiting_response_created=True,
        request_text=json.dumps(request_payload, separators=(",", ":")),
        previous_response_id="resp_anchor",
        fresh_upstream_request_text=json.dumps(fresh_request_payload, separators=(",", ":")),
        fresh_upstream_request_is_retry_safe=True,
    )
    pending_requests = deque([pending_request])
    upstream_control = proxy_service._WebSocketUpstreamControl()
    upstream_payload = {
        "type": "error",
        "status": 400,
        "error": {
            "type": "invalid_request_error",
            "code": "previous_response_not_found",
            "message": "Previous response with id 'resp_anchor' not found.",
            "param": "previous_response_id",
        },
    }
    upstream_text = json.dumps(upstream_payload, separators=(",", ":"))

    await service._process_upstream_websocket_text(
        upstream_text,
        account=account,
        account_id_value=account.id,
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        api_key=None,
        upstream_control=upstream_control,
        response_create_gate=asyncio.Semaphore(1),
    )

    handle_stream_error.assert_not_awaited()
    assert upstream_control.reconnect_requested is True
    assert upstream_control.suppress_downstream_event is True
    assert upstream_control.replay_request_state is pending_request
    assert pending_request.replay_count == 1
    # The retry must strip the stale ``previous_response_id`` and use the
    # prepared retry-safe text instead of the original anchored payload.
    assert pending_request.previous_response_id is None
    assert pending_request.request_text == json.dumps(fresh_request_payload, separators=(",", ":"))
    # Retry-safety flag is consumed (set back to False) so we don't loop.
    assert pending_request.fresh_upstream_request_is_retry_safe is False


@pytest.mark.asyncio
async def test_process_upstream_websocket_text_masks_previous_response_not_found_for_unique_followup_request(
    monkeypatch,
):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    finalize_request_state = AsyncMock()
    handle_stream_error = AsyncMock()
    account = _make_account("acc_ws_prev_not_found_followup_match")

    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize_request_state)
    monkeypatch.setattr(service, "_handle_stream_error", handle_stream_error)

    inflight_request = proxy_service._WebSocketRequestState(
        request_id="ws_req_inflight",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        awaiting_response_created=True,
        request_text=json.dumps(
            {
                "type": "response.create",
                "model": "gpt-5.1",
                "input": [{"role": "user", "content": [{"type": "input_text", "text": "first"}]}],
            },
            separators=(",", ":"),
        ),
    )
    followup_request = proxy_service._WebSocketRequestState(
        request_id="ws_req_followup_prev_not_found",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        awaiting_response_created=True,
        request_text=json.dumps(
            {
                "type": "response.create",
                "model": "gpt-5.1",
                "previous_response_id": "resp_anchor",
                "input": [{"role": "user", "content": [{"type": "input_text", "text": "continue"}]}],
            },
            separators=(",", ":"),
        ),
        previous_response_id="resp_anchor",
    )
    pending_requests = deque([inflight_request, followup_request])
    upstream_control = proxy_service._WebSocketUpstreamControl()
    upstream_payload = {
        "type": "error",
        "status": 400,
        "error": {
            "type": "invalid_request_error",
            "code": "previous_response_not_found",
            "message": "Previous response with id 'resp_anchor' not found.",
            "param": "previous_response_id",
        },
    }
    upstream_text = json.dumps(upstream_payload, separators=(",", ":"))

    downstream_text = await service._process_upstream_websocket_text(
        upstream_text,
        account=account,
        account_id_value=account.id,
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        api_key=None,
        upstream_control=upstream_control,
        response_create_gate=asyncio.Semaphore(1),
    )

    assert '"type":"response.failed"' in downstream_text
    assert '"code":"stream_incomplete"' in downstream_text
    assert "previous_response_not_found" not in downstream_text
    handle_stream_error.assert_not_awaited()
    finalize_request_state.assert_awaited_once()
    finalize_call = finalize_request_state.await_args
    assert finalize_call is not None
    assert finalize_call.args[0] is followup_request
    assert finalize_call.kwargs["event_type"] == "response.failed"
    assert upstream_control.reconnect_requested is True
    assert upstream_control.suppress_downstream_event is False
    assert list(pending_requests) == [inflight_request]


def test_maybe_rewrite_websocket_previous_response_not_found_rewrites_response_failed_event():
    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_prev_nf",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        response_id="resp_prev_nf",
        previous_response_id="resp_prev_anchor",
    )
    original_payload: dict[str, JsonValue] = {
        "type": "response.failed",
        "response": {
            "id": "resp_prev_nf",
            "status": "failed",
            "error": {
                "type": "invalid_request_error",
                "code": "previous_response_not_found",
                "message": "Previous response with id 'resp_prev_anchor' not found.",
                "param": "previous_response_id",
            },
        },
    }
    original_text = json.dumps(original_payload, separators=(",", ":"))
    original_event = parse_sse_event(f"data: {original_text}\n\n")
    assert original_event is not None
    original_event_type = proxy_service._event_type_from_payload(original_event, original_payload)
    upstream_control = proxy_service._WebSocketUpstreamControl()

    _, rewritten_payload, rewritten_event_type, rewritten_text = (
        proxy_service._maybe_rewrite_websocket_previous_response_not_found_event(
            request_state=request_state,
            event=original_event,
            payload=original_payload,
            event_type=original_event_type,
            upstream_control=upstream_control,
            original_text=original_text,
        )
    )

    assert upstream_control.reconnect_requested is True
    assert rewritten_event_type == "response.failed"
    assert rewritten_payload is not None
    response_payload = cast(dict[str, JsonValue], rewritten_payload.get("response"))
    error_payload = cast(dict[str, JsonValue], response_payload.get("error"))
    assert error_payload["code"] == "stream_incomplete"
    assert error_payload["message"] == "Upstream websocket closed before response.completed"
    assert "previous_response_not_found" not in rewritten_text


def test_maybe_rewrite_websocket_previous_response_invalid_request_error_rewrites_when_message_is_not_found():
    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_prev_invalid",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        response_id="resp_prev_invalid",
        previous_response_id="resp_prev_anchor",
    )
    original_payload: dict[str, JsonValue] = {
        "type": "error",
        "status": 400,
        "error": {
            "type": "invalid_request_error",
            "code": "invalid_request_error",
            "message": "Previous response with id 'resp_prev_anchor' not found.",
            "param": "previous_response_id",
        },
    }
    original_text = json.dumps(original_payload, separators=(",", ":"))
    original_event = parse_sse_event(f"data: {original_text}\n\n")
    assert original_event is not None
    original_event_type = proxy_service._event_type_from_payload(original_event, original_payload)
    upstream_control = proxy_service._WebSocketUpstreamControl()

    _, rewritten_payload, rewritten_event_type, rewritten_text = (
        proxy_service._maybe_rewrite_websocket_previous_response_not_found_event(
            request_state=request_state,
            event=original_event,
            payload=original_payload,
            event_type=original_event_type,
            upstream_control=upstream_control,
            original_text=original_text,
        )
    )

    assert upstream_control.reconnect_requested is True
    assert rewritten_event_type == "response.failed"
    assert rewritten_payload is not None
    response_payload = cast(dict[str, JsonValue], rewritten_payload.get("response"))
    error_payload = cast(dict[str, JsonValue], response_payload.get("error"))
    assert error_payload["code"] == "stream_incomplete"
    assert error_payload["message"] == "Upstream websocket closed before response.completed"
    assert "previous_response_not_found" not in rewritten_text


def test_maybe_rewrite_websocket_previous_response_invalid_request_error_does_not_rewrite_other_message():
    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_prev_invalid_other_message",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        response_id="resp_prev_invalid_other_message",
        previous_response_id="resp_prev_anchor",
    )
    original_payload: dict[str, JsonValue] = {
        "type": "error",
        "status": 400,
        "error": {
            "type": "invalid_request_error",
            "code": "invalid_request_error",
            "message": "Invalid request payload",
            "param": "previous_response_id",
        },
    }
    original_text = json.dumps(original_payload, separators=(",", ":"))
    original_event = parse_sse_event(f"data: {original_text}\n\n")
    assert original_event is not None
    original_event_type = proxy_service._event_type_from_payload(original_event, original_payload)
    upstream_control = proxy_service._WebSocketUpstreamControl()

    _, rewritten_payload, rewritten_event_type, rewritten_text = (
        proxy_service._maybe_rewrite_websocket_previous_response_not_found_event(
            request_state=request_state,
            event=original_event,
            payload=original_payload,
            event_type=original_event_type,
            upstream_control=upstream_control,
            original_text=original_text,
        )
    )

    assert upstream_control.reconnect_requested is False
    assert rewritten_event_type == original_event_type
    assert rewritten_payload == original_payload
    assert rewritten_text == original_text


def test_maybe_rewrite_websocket_previous_response_invalid_request_error_does_not_rewrite_other_param():
    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_prev_invalid_other_param",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        response_id="resp_prev_invalid_other_param",
        previous_response_id="resp_prev_anchor",
    )
    original_payload: dict[str, JsonValue] = {
        "type": "error",
        "status": 400,
        "error": {
            "type": "invalid_request_error",
            "code": "invalid_request_error",
            "message": "Invalid request payload",
            "param": "input",
        },
    }
    original_text = json.dumps(original_payload, separators=(",", ":"))
    original_event = parse_sse_event(f"data: {original_text}\n\n")
    assert original_event is not None
    original_event_type = proxy_service._event_type_from_payload(original_event, original_payload)
    upstream_control = proxy_service._WebSocketUpstreamControl()

    _, rewritten_payload, rewritten_event_type, rewritten_text = (
        proxy_service._maybe_rewrite_websocket_previous_response_not_found_event(
            request_state=request_state,
            event=original_event,
            payload=original_payload,
            event_type=original_event_type,
            upstream_control=upstream_control,
            original_text=original_text,
        )
    )

    assert upstream_control.reconnect_requested is False
    assert rewritten_event_type == original_event_type
    assert rewritten_payload == original_payload
    assert rewritten_text == original_text


def test_http_bridge_should_attempt_local_previous_response_recovery_invalid_request_requires_not_found_message():
    recoverable_error = proxy_module.ProxyResponseError(
        400,
        {
            "error": {
                "type": "invalid_request_error",
                "code": "invalid_request_error",
                "message": "Previous response with id 'resp_prev_anchor' not found.",
                "param": "previous_response_id",
            }
        },
    )
    non_recoverable_error = proxy_module.ProxyResponseError(
        400,
        {
            "error": {
                "type": "invalid_request_error",
                "code": "invalid_request_error",
                "message": "Invalid request payload",
                "param": "previous_response_id",
            }
        },
    )

    assert proxy_service._http_bridge_should_attempt_local_previous_response_recovery(recoverable_error) is True
    assert proxy_service._http_bridge_should_attempt_local_previous_response_recovery(non_recoverable_error) is False


def test_http_bridge_should_rollover_after_context_overflow():
    context_overflow_error = proxy_module.ProxyResponseError(
        400,
        {
            "error": {
                "type": "invalid_request_error",
                "code": "context_length_exceeded",
                "message": "Your input exceeds the context window of this model.",
            }
        },
    )
    unrelated_error = proxy_module.ProxyResponseError(
        400,
        {
            "error": {
                "type": "invalid_request_error",
                "code": "invalid_request_error",
                "message": "Invalid request payload",
                "param": "input",
            }
        },
    )
    hard_key = proxy_service._HTTPBridgeSessionKey("turn_state_header", "turn-hard", None)
    soft_key = proxy_service._HTTPBridgeSessionKey("prompt_cache", "cache-soft", None)

    assert proxy_service._http_bridge_should_rollover_after_context_overflow(context_overflow_error) is True
    assert (
        proxy_service._http_bridge_should_rollover_after_context_overflow(context_overflow_error, key=hard_key) is False
    )
    assert (
        proxy_service._http_bridge_should_rollover_after_context_overflow(context_overflow_error, key=soft_key) is True
    )
    assert proxy_service._http_bridge_should_rollover_after_context_overflow(unrelated_error) is False


def test_maybe_rewrite_websocket_previous_response_not_found_leaves_non_previous_request_unchanged():
    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_plain",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
    )
    original_payload: dict[str, JsonValue] = {
        "type": "error",
        "status": 400,
        "error": {
            "type": "invalid_request_error",
            "code": "previous_response_not_found",
            "message": "Previous response with id 'resp_any' not found.",
            "param": "previous_response_id",
        },
    }
    original_text = json.dumps(original_payload, separators=(",", ":"))
    original_event = parse_sse_event(f"data: {original_text}\n\n")
    assert original_event is not None
    original_event_type = proxy_service._event_type_from_payload(original_event, original_payload)
    upstream_control = proxy_service._WebSocketUpstreamControl()

    _, rewritten_payload, rewritten_event_type, rewritten_text = (
        proxy_service._maybe_rewrite_websocket_previous_response_not_found_event(
            request_state=request_state,
            event=original_event,
            payload=original_payload,
            event_type=original_event_type,
            upstream_control=upstream_control,
            original_text=original_text,
        )
    )

    assert upstream_control.reconnect_requested is False
    assert rewritten_event_type == original_event_type
    assert rewritten_payload == original_payload
    assert rewritten_text == original_text


def test_sanitize_websocket_connect_failure_rewrites_previous_response_not_found(monkeypatch, caplog):
    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_prev_connect_failure",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        previous_response_id="resp_prev_anchor",
    )
    counter = _ObservedCounter()
    monkeypatch.setattr(proxy_service, "PROMETHEUS_AVAILABLE", True)
    monkeypatch.setattr(proxy_service, "continuity_fail_closed_total", counter, raising=False)
    caplog.set_level(logging.WARNING, logger="app.modules.proxy.service")
    original_payload = proxy_module.openai_error(
        "previous_response_not_found",
        "Previous response with id 'resp_prev_anchor' not found.",
        error_type="invalid_request_error",
    )
    original_payload["error"]["param"] = "previous_response_id"

    (
        rewritten_status,
        rewritten_payload,
        rewritten_error_code,
        rewritten_error_message,
    ) = proxy_service._sanitize_websocket_connect_failure(
        request_state=request_state,
        status_code=400,
        payload=original_payload,
        error_code="previous_response_not_found",
        error_message="Previous response with id 'resp_prev_anchor' not found.",
    )

    assert rewritten_status == 502
    assert rewritten_payload["error"]["code"] == "stream_incomplete"
    assert rewritten_payload["error"]["message"] == "Upstream websocket closed before response.completed"
    assert rewritten_payload["error"]["type"] == "server_error"
    assert rewritten_error_code == "stream_incomplete"
    assert rewritten_error_message == "Upstream websocket closed before response.completed"
    assert "continuity_fail_closed surface=websocket_connect reason=previous_response_not_found" in caplog.text
    assert "resp_prev_anchor" not in caplog.text
    assert counter.samples == [
        {
            "labels": {"surface": "websocket_connect", "reason": "previous_response_not_found"},
            "value": 1.0,
        }
    ]


def test_sanitize_websocket_connect_failure_rewrites_invalid_request_previous_response_not_found():
    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_prev_connect_failure_invalid_request",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        previous_response_id="resp_prev_anchor",
    )
    original_payload = proxy_module.openai_error(
        "invalid_request_error",
        "Previous response with id 'resp_prev_anchor' not found.",
        error_type="invalid_request_error",
    )
    original_payload["error"]["param"] = "previous_response_id"

    (
        rewritten_status,
        rewritten_payload,
        rewritten_error_code,
        rewritten_error_message,
    ) = proxy_service._sanitize_websocket_connect_failure(
        request_state=request_state,
        status_code=400,
        payload=original_payload,
        error_code="invalid_request_error",
        error_message="Previous response with id 'resp_prev_anchor' not found.",
    )

    assert rewritten_status == 502
    assert rewritten_payload["error"]["code"] == "stream_incomplete"
    assert rewritten_payload["error"]["message"] == "Upstream websocket closed before response.completed"
    assert rewritten_payload["error"]["type"] == "server_error"
    assert rewritten_error_code == "stream_incomplete"
    assert rewritten_error_message == "Upstream websocket closed before response.completed"


@pytest.mark.asyncio
async def test_emit_websocket_connect_failure_releases_response_create_gate(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_connect_failure_gate",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        awaiting_response_created=True,
    )
    response_create_gate = asyncio.Semaphore(1)
    await response_create_gate.acquire()
    request_state.response_create_gate_acquired = True
    request_state.response_create_gate = response_create_gate

    release_reservation = AsyncMock()
    monkeypatch.setattr(service, "_release_websocket_reservation", release_reservation)

    websocket_send = AsyncMock()
    websocket = cast(WebSocket, SimpleNamespace(send_text=websocket_send))

    await service._emit_websocket_connect_failure(
        websocket,
        client_send_lock=anyio.Lock(),
        account_id="acc_connect_failure",
        api_key=None,
        request_state=request_state,
        status_code=502,
        payload=openai_error(
            "upstream_unavailable",
            "Previous response owner account is unavailable; retry later.",
            error_type="server_error",
        ),
        error_code="upstream_unavailable",
        error_message="Previous response owner account is unavailable; retry later.",
    )

    release_reservation.assert_awaited_once_with(None)
    assert response_create_gate.locked() is False
    assert request_state.awaiting_response_created is False
    assert request_state.response_create_gate_acquired is False
    assert request_state.response_create_gate is None


@pytest.mark.asyncio
async def test_emit_websocket_terminal_error_releases_response_create_gate():
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_terminal_failure_gate",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        awaiting_response_created=True,
    )
    response_create_gate = asyncio.Semaphore(1)
    await response_create_gate.acquire()
    request_state.response_create_gate_acquired = True
    request_state.response_create_gate = response_create_gate

    websocket_send = AsyncMock()
    websocket = cast(WebSocket, SimpleNamespace(send_text=websocket_send))

    await service._emit_websocket_terminal_error(
        websocket,
        client_send_lock=anyio.Lock(),
        request_state=request_state,
        error_code="upstream_unavailable",
        error_message="Previous response owner lookup failed; retry later.",
    )

    assert response_create_gate.locked() is False
    assert request_state.awaiting_response_created is False
    assert request_state.response_create_gate_acquired is False
    assert request_state.response_create_gate is None


def test_sanitize_websocket_connect_failure_leaves_unrelated_previous_response_error_unchanged():
    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_prev_connect_failure_unrelated",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        previous_response_id="resp_prev_anchor",
    )
    original_payload = proxy_module.openai_error(
        "invalid_request_error",
        "Invalid request payload",
        error_type="invalid_request_error",
    )
    original_payload["error"]["param"] = "previous_response_id"

    (
        rewritten_status,
        rewritten_payload,
        rewritten_error_code,
        rewritten_error_message,
    ) = proxy_service._sanitize_websocket_connect_failure(
        request_state=request_state,
        status_code=400,
        payload=original_payload,
        error_code="invalid_request_error",
        error_message="Invalid request payload",
    )

    assert rewritten_status == 400
    assert rewritten_payload == original_payload
    assert rewritten_error_code == "invalid_request_error"
    assert rewritten_error_message == "Invalid request payload"


@pytest.mark.asyncio
async def test_stream_responses_budget_exhaustion_emits_timeout_event(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))

    runtime_values = dict(settings.__dict__)
    runtime_values["proxy_request_budget_seconds"] = 0.0
    runtime_settings = SimpleNamespace(**runtime_values)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: runtime_settings)
    monkeypatch.setattr(proxy_service.time, "monotonic", lambda: 100.0)

    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    event = json.loads(chunks[0].split("data: ", 1)[1])
    assert event["response"]["error"]["code"] == "upstream_request_timeout"
    assert request_logs.calls[0]["status"] == "error"
    assert request_logs.calls[0]["error_code"] == "upstream_request_timeout"
    assert request_logs.calls[0]["error_message"] == "Proxy request budget exhausted"
    assert request_logs.calls[0]["account_id"] is None
    assert request_logs.calls[0]["transport"] == "http"


@pytest.mark.asyncio
async def test_stream_selection_budget_exhaustion_emits_timeout_event(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service,
        "_select_account_with_budget",
        AsyncMock(
            side_effect=proxy_module.ProxyResponseError(
                502,
                openai_error("upstream_unavailable", "Proxy request budget exhausted"),
            )
        ),
    )

    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    event = json.loads(chunks[0].split("data: ", 1)[1])
    assert event["response"]["error"]["code"] == "upstream_request_timeout"
    assert request_logs.calls[0]["status"] == "error"
    assert request_logs.calls[0]["error_code"] == "upstream_request_timeout"
    assert request_logs.calls[0]["error_message"] == "Proxy request budget exhausted"
    assert request_logs.calls[0]["account_id"] is None


@pytest.mark.asyncio
async def test_stream_refresh_timeout_emits_upstream_unavailable_and_logs(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_stream_refresh_timeout")

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )

    async def failing_ensure_fresh(account, *, force: bool = False, timeout_seconds: float | None = None):
        raise asyncio.TimeoutError

    monkeypatch.setattr(service, "_ensure_fresh", failing_ensure_fresh)

    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    event = json.loads(chunks[0].split("data: ", 1)[1])
    assert event["response"]["error"]["code"] == "upstream_unavailable"
    assert event["response"]["error"]["message"] == "Request to upstream timed out"
    assert request_logs.calls[-1]["account_id"] == account.id
    assert request_logs.calls[-1]["status"] == "error"
    assert request_logs.calls[-1]["error_code"] == "upstream_unavailable"
    assert request_logs.calls[-1]["error_message"] == "Request to upstream timed out"


@pytest.mark.asyncio
async def test_stream_forced_refresh_timeout_emits_upstream_unavailable_and_logs(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_stream_forced_refresh_timeout")

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )

    async def fake_ensure_fresh(account, *, force: bool = False, timeout_seconds: float | None = None):
        if force:
            raise asyncio.TimeoutError
        return account

    async def failing_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        raise proxy_module.ProxyResponseError(401, openai_error("invalid_api_key", "token expired"))
        if False:
            yield ""

    monkeypatch.setattr(service, "_ensure_fresh", fake_ensure_fresh)
    monkeypatch.setattr(proxy_service, "core_stream_responses", failing_stream)

    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    event = json.loads(chunks[0].split("data: ", 1)[1])
    assert event["response"]["error"]["code"] == "upstream_unavailable"
    assert event["response"]["error"]["message"] == "Request to upstream timed out"
    assert request_logs.calls[-1]["account_id"] == account.id
    assert request_logs.calls[-1]["status"] == "error"
    assert request_logs.calls[-1]["error_code"] == "upstream_unavailable"
    assert request_logs.calls[-1]["error_message"] == "Request to upstream timed out"


@pytest.mark.asyncio
async def test_stream_refresh_budget_is_recomputed_after_selection(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_refresh_budget")
    captured: dict[str, float | None] = {}

    runtime_values = dict(settings.__dict__)
    runtime_values["proxy_request_budget_seconds"] = 10.0
    runtime_settings = SimpleNamespace(**runtime_values)
    monotonic_calls = {"count": 0}

    def fake_monotonic():
        monotonic_calls["count"] += 1
        return 100.0 if monotonic_calls["count"] < 4 else 107.0

    async def fake_ensure_fresh(account, *, force: bool = False, timeout_seconds: float | None = None):
        captured["timeout_seconds"] = timeout_seconds
        return account

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield 'data: {"type":"response.completed","response":{"id":"resp_budget"}}\n\n'

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: runtime_settings)
    monkeypatch.setattr(proxy_service.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", fake_ensure_fresh)
    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)
    monkeypatch.setattr(service, "_settle_stream_api_key_usage", AsyncMock(return_value=True))

    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    event = json.loads(chunks[0].split("data: ", 1)[1])
    assert event["type"] == "response.completed"
    assert captured["timeout_seconds"] == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_stream_attempt_timeout_overrides_follow_remaining_budget(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_stream_attempt_budget")
    overrides: list[dict[str, float | None]] = []

    remaining_budget_values = iter((10.0, 10.0, 3.0))

    def fake_remaining_budget(deadline: float) -> float:
        del deadline
        try:
            return next(remaining_budget_values)
        except StopIteration:
            return 3.0

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield 'data: {"type":"response.completed","response":{"id":"resp_budget"}}\n\n'

    def fake_push_stream_timeout_overrides(
        *,
        connect_timeout_seconds: float | None = None,
        idle_timeout_seconds: float | None = None,
        total_timeout_seconds: float | None = None,
    ) -> tuple[float | None, float | None, float | None]:
        overrides.append(
            {
                "connect": connect_timeout_seconds,
                "idle": idle_timeout_seconds,
                "total": total_timeout_seconds,
            }
        )
        return (None, None, None)

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "_remaining_budget_seconds", fake_remaining_budget)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(service, "_settle_stream_api_key_usage", AsyncMock(return_value=True))
    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_service, "push_stream_timeout_overrides", fake_push_stream_timeout_overrides)
    monkeypatch.setattr(proxy_service, "pop_stream_timeout_overrides", lambda tokens: None)

    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    event = json.loads(chunks[0].split("data: ", 1)[1])
    assert event["type"] == "response.completed"
    assert overrides == [{"connect": 3.0, "idle": 3.0, "total": 3.0}]


@pytest.mark.asyncio
async def test_stream_forced_refresh_reapplies_idle_and_total_budget_overrides(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_stream_forced_refresh_budget")
    overrides: list[dict[str, float | None]] = []
    stream_call_count = {"count": 0}

    remaining_budget_values = iter((10.0, 10.0, 10.0, 6.0, 2.0))

    def fake_remaining_budget(deadline: float) -> float:
        del deadline
        try:
            return next(remaining_budget_values)
        except StopIteration:
            return 2.0

    async def fake_ensure_fresh(account, *, force: bool = False, timeout_seconds: float | None = None):
        del timeout_seconds
        return account

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        stream_call_count["count"] += 1
        if stream_call_count["count"] == 1:
            raise proxy_module.ProxyResponseError(401, openai_error("invalid_api_key", "token expired"))
        yield 'data: {"type":"response.completed","response":{"id":"resp_retry"}}\n\n'

    def fake_push_stream_timeout_overrides(
        *,
        connect_timeout_seconds: float | None = None,
        idle_timeout_seconds: float | None = None,
        total_timeout_seconds: float | None = None,
    ) -> tuple[float | None, float | None, float | None]:
        overrides.append(
            {
                "connect": connect_timeout_seconds,
                "idle": idle_timeout_seconds,
                "total": total_timeout_seconds,
            }
        )
        return (None, None, None)

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "_remaining_budget_seconds", fake_remaining_budget)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", fake_ensure_fresh)
    monkeypatch.setattr(service, "_settle_stream_api_key_usage", AsyncMock(return_value=True))
    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_service, "push_stream_timeout_overrides", fake_push_stream_timeout_overrides)
    monkeypatch.setattr(proxy_service, "pop_stream_timeout_overrides", lambda tokens: None)

    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    event = json.loads(chunks[0].split("data: ", 1)[1])
    assert event["type"] == "response.completed"
    assert len(overrides) == 2
    assert overrides[-1] == {"connect": 2.0, "idle": 2.0, "total": 2.0}
    assert all(override["connect"] == override["idle"] == override["total"] for override in overrides)


@pytest.mark.asyncio
async def test_stream_midstream_generic_failure_is_neutral_to_account_health(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_midstream_failure")
    record_error = AsyncMock()
    record_success = AsyncMock()

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service._load_balancer, "record_error", record_error)
    monkeypatch.setattr(service._load_balancer, "record_success", record_success)
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(service, "_settle_stream_api_key_usage", AsyncMock(return_value=True))

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield 'data: {"type":"response.output_text.delta","delta":"hi"}\n\n'
        yield (
            'data: {"type":"response.failed","response":{"error":{"code":"upstream_request_timeout",'
            '"message":"Proxy request budget exhausted"}}}\n\n'
        )

    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)

    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    last_event = json.loads(chunks[-1].split("data: ", 1)[1])
    assert last_event["type"] == "response.failed"
    assert last_event["response"]["error"]["code"] == "upstream_request_timeout"
    record_error.assert_not_awaited()
    record_success.assert_not_awaited()
    assert request_logs.calls[0]["account_id"] == account.id
    assert request_logs.calls[0]["status"] == "error"
    assert request_logs.calls[0]["error_code"] == "upstream_request_timeout"


@pytest.mark.asyncio
async def test_stream_incomplete_records_success_without_account_error(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_incomplete_stream")
    record_error = AsyncMock()
    record_success = AsyncMock()

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service._load_balancer, "record_error", record_error)
    monkeypatch.setattr(service._load_balancer, "record_success", record_success)
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(service, "_settle_stream_api_key_usage", AsyncMock(return_value=True))

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield (
            'data: {"type":"response.incomplete","response":{"status":"incomplete","usage":'
            '{"input_tokens":1,"output_tokens":1},"incomplete_details":{"reason":"max_output_tokens"}}}\n\n'
        )

    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)

    payload = ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True})

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    event = json.loads(chunks[0].split("data: ", 1)[1])
    assert event["type"] == "response.incomplete"
    record_success.assert_awaited_once_with(account)
    record_error.assert_not_awaited()
    assert request_logs.calls[0]["status"] == "error"
    assert request_logs.calls[0]["error_code"] is None


@pytest.mark.asyncio
async def test_stream_previous_response_not_found_proxy_error_is_masked_to_stream_incomplete(monkeypatch, caplog):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_prev_missing_stream")
    record_error = AsyncMock()
    record_success = AsyncMock()
    counter = _ObservedCounter()

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "PROMETHEUS_AVAILABLE", True)
    monkeypatch.setattr(proxy_service, "continuity_fail_closed_total", counter, raising=False)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service._load_balancer, "record_error", record_error)
    monkeypatch.setattr(service._load_balancer, "record_success", record_success)
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(service, "_settle_stream_api_key_usage", AsyncMock(return_value=True))

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, account_id, base_url, raise_for_status, kwargs
        error_payload = openai_error(
            "previous_response_not_found",
            "Previous response with id 'resp_prev_anchor' not found.",
            error_type="invalid_request_error",
        )
        error_payload["error"]["param"] = "previous_response_id"
        raise proxy_module.ProxyResponseError(400, error_payload)
        if False:
            yield ""

    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [],
            "stream": True,
            "previous_response_id": "resp_prev_anchor",
        }
    )

    caplog.set_level(logging.WARNING, logger="app.modules.proxy.service")
    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    event = json.loads(chunks[0].split("data: ", 1)[1])
    assert event["type"] == "response.failed"
    assert event["response"]["error"]["code"] == "stream_incomplete"
    assert event["response"]["error"]["message"] == "Upstream websocket closed before response.completed"
    assert "previous_response_not_found" not in chunks[0]
    assert request_logs.lookup_calls == [("resp_prev_anchor", None, "sid-stream")]
    assert request_logs.calls[0]["error_code"] == "stream_incomplete"
    assert "continuity_fail_closed surface=http_stream reason=previous_response_not_found" in caplog.text
    assert "resp_prev_anchor" not in caplog.text
    assert counter.samples == [
        {
            "labels": {"surface": "http_stream", "reason": "previous_response_not_found"},
            "value": 1.0,
        }
    ]
    record_error.assert_not_awaited()
    record_success.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_previous_response_owner_usage_limit_fails_closed(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account_owner = _make_account("acc_prev_owner_stream")
    account_other = _make_account("acc_other_stream")
    request_logs.response_owner_by_id[("resp_prev_anchor", None, "sid-stream")] = account_owner.id
    select_account_calls: list[dict[str, object]] = []
    handle_stream_error = AsyncMock(return_value={"failure_class": "rate_limit"})
    record_success = AsyncMock()

    async def fake_select_account(**kwargs):
        select_account_calls.append(dict(kwargs))
        account_ids = kwargs.get("account_ids")
        exclude_account_ids = set(cast(set[str], kwargs.get("exclude_account_ids", set())))
        if account_ids == {account_owner.id}:
            return AccountSelection(account=account_owner, error_message=None)
        if account_owner.id in exclude_account_ids:
            return AccountSelection(account=account_other, error_message=None)
        return AccountSelection(account=account_owner, error_message=None)

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(service._load_balancer, "select_account", AsyncMock(side_effect=fake_select_account))
    monkeypatch.setattr(service._load_balancer, "record_success", record_success)
    monkeypatch.setattr(service, "_handle_stream_error", handle_stream_error)
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account_owner))
    monkeypatch.setattr(service, "_settle_stream_api_key_usage", AsyncMock(return_value=True))

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, account_id, base_url, raise_for_status, kwargs
        yield (
            'data: {"type":"response.failed","response":{"id":"resp_owner_limit","status":"failed",'
            '"error":{"code":"usage_limit_reached","message":"usage limit reached"},'
            '"usage":{"input_tokens":0,"output_tokens":0,"total_tokens":0}}}\n\n'
        )

    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [],
            "stream": True,
            "previous_response_id": "resp_prev_anchor",
        }
    )

    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    event = json.loads(chunks[0].split("data: ", 1)[1])
    assert event["type"] == "response.failed"
    assert event["response"]["error"]["code"] == "upstream_unavailable"
    assert event["response"]["error"]["message"] == "Previous response owner account is unavailable; retry later."
    assert request_logs.lookup_calls == [("resp_prev_anchor", None, "sid-stream")]
    assert request_logs.calls[0]["error_code"] == "upstream_unavailable"
    assert request_logs.calls[0]["account_id"] == account_owner.id
    assert len(select_account_calls) == 1
    assert select_account_calls[0]["account_ids"] == {account_owner.id}
    handle_stream_error.assert_awaited_once()
    handle_await_args = handle_stream_error.await_args
    assert handle_await_args is not None
    assert handle_await_args.args[0] == account_owner
    assert handle_await_args.args[2] == "usage_limit_reached"
    record_success.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_selection_fail_closed_records_owner_unavailable_metric(monkeypatch, caplog):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    request_logs.response_owner_by_id[("resp_prev_anchor", None, "sid-stream")] = "acc_prev_owner_stream"
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    counter = _ObservedCounter()

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "PROMETHEUS_AVAILABLE", True)
    monkeypatch.setattr(proxy_service, "continuity_fail_closed_total", counter, raising=False)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=None, error_message="No active accounts available")),
    )

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [],
            "stream": True,
            "previous_response_id": "resp_prev_anchor",
        }
    )

    caplog.set_level(logging.WARNING, logger="app.modules.proxy.service")
    chunks = [chunk async for chunk in service.stream_responses(payload, {"session_id": "sid-stream"})]

    event = json.loads(chunks[0].split("data: ", 1)[1])
    assert event["type"] == "response.failed"
    assert event["response"]["error"]["code"] == "upstream_unavailable"
    assert event["response"]["error"]["message"] == "Previous response owner account is unavailable; retry later."
    assert request_logs.calls[0]["account_id"] == "acc_prev_owner_stream"
    assert "continuity_fail_closed surface=http_stream reason=owner_account_unavailable" in caplog.text
    assert "resp_prev_anchor" not in caplog.text
    assert counter.samples == [
        {
            "labels": {"surface": "http_stream", "reason": "owner_account_unavailable"},
            "value": 1.0,
        }
    ]


@pytest.mark.asyncio
async def test_compact_responses_budget_exhaustion_returns_upstream_unavailable(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_compact_budget")

    runtime_values = dict(settings.__dict__)
    runtime_values["compact_request_budget_seconds"] = 0.0
    runtime_settings = SimpleNamespace(**runtime_values)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: runtime_settings)
    monkeypatch.setattr(proxy_service.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))

    payload = ResponsesCompactRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": []})

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service.compact_responses(payload, {"session_id": "sid-compact"})

    exc = _assert_proxy_response_error(exc_info.value)
    assert exc.status_code == 502
    assert _proxy_error_code(exc) == "upstream_unavailable"
    assert request_logs.calls[0]["error_code"] == "upstream_unavailable"
    assert request_logs.calls[0]["transport"] == "http"


@pytest.mark.asyncio
async def test_compact_responses_records_transient_error_for_generic_upstream_failure(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_compact_error")
    record_error = AsyncMock()
    record_success = AsyncMock()

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service._load_balancer, "record_error", record_error)
    monkeypatch.setattr(service._load_balancer, "record_success", record_success)
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))

    async def failing_compact(payload, headers, access_token, account_id):
        raise proxy_module.ProxyResponseError(502, openai_error("upstream_unavailable", "late"))

    monkeypatch.setattr(proxy_service, "core_compact_responses", failing_compact)

    payload = ResponsesCompactRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": []})

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service.compact_responses(payload, {"session_id": "sid-compact"})

    exc = _assert_proxy_response_error(exc_info.value)
    assert exc.status_code == 502
    assert _proxy_error_code(exc) == "upstream_unavailable"
    record_error.assert_awaited_once_with(account)
    record_success.assert_not_awaited()


@pytest.mark.asyncio
async def test_compact_responses_surfaces_local_create_overload_without_penalizing_account(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    settings.proxy_compact_response_create_limit = 1
    settings.proxy_admission_wait_timeout_seconds = 0.05
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_compact_local_overload")
    record_error = AsyncMock()

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    failing_upstream = AsyncMock()
    monkeypatch.setattr(proxy_service, "core_compact_responses", failing_upstream)
    monkeypatch.setattr(service._load_balancer, "record_error", record_error)

    lease = await service._get_work_admission().acquire_response_create(compact=True)
    payload = ResponsesCompactRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": []})
    try:
        with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
            await service.compact_responses(payload, {"session_id": "sid-compact"})
    finally:
        lease.release()

    exc = _assert_proxy_response_error(exc_info.value)
    assert exc.status_code == 429
    assert _proxy_error_code(exc) == "proxy_overloaded"
    failing_upstream.assert_not_awaited()
    record_error.assert_not_awaited()
    assert request_logs.calls[0]["error_code"] == "proxy_overloaded"


@pytest.mark.asyncio
async def test_ensure_fresh_skips_token_refresh_admission_for_fresh_account(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    settings.proxy_token_refresh_limit = 1
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    account = _make_account("acc_fresh_no_refresh")

    async def fake_ensure_fresh(self, target, *, force: bool = False):
        assert force is False
        return target

    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service.AuthManager, "ensure_fresh", fake_ensure_fresh)

    lease = await service._get_work_admission().acquire_token_refresh()
    try:
        refreshed = await service._ensure_fresh(account, force=False)
    finally:
        lease.release()

    assert refreshed is account


@pytest.mark.asyncio
async def test_ensure_fresh_same_stale_account_joins_singleflight_before_refresh_admission(monkeypatch):
    auth_manager_module._clear_refresh_singleflight_state()
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    settings.proxy_token_refresh_limit = 1
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    encryptor = TokenEncryptor()
    stale_refresh = utcnow().replace(year=utcnow().year - 1)
    account_a = Account(
        id="acc_refresh_singleflight",
        chatgpt_account_id="acc_refresh_singleflight",
        email="singleflight@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-old"),
        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
        id_token_encrypted=encryptor.encrypt("id-old"),
        last_refresh=stale_refresh,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    account_b = Account(**{column.name: getattr(account_a, column.name) for column in Account.__table__.columns})
    started = asyncio.Event()
    release = asyncio.Event()
    refresh_calls = 0

    async def fake_refresh_access_token(_: str):
        nonlocal refresh_calls
        refresh_calls += 1
        started.set()
        await release.wait()
        return auth_manager_module.TokenRefreshResult(
            access_token="access-new",
            refresh_token="refresh-new",
            id_token="id-new",
            account_id="acc_refresh_singleflight",
            plan_type="plus",
            email="singleflight@example.com",
        )

    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh_access_token)

    first = asyncio.create_task(service._ensure_fresh(account_a, force=True))
    await started.wait()
    second = asyncio.create_task(service._ensure_fresh(account_b, force=True))
    await asyncio.sleep(0.01)
    assert not second.done()

    release.set()
    refreshed_a, refreshed_b = await asyncio.gather(first, second)

    assert refresh_calls == 1
    assert refreshed_a.chatgpt_account_id == "acc_refresh_singleflight"
    assert refreshed_b.chatgpt_account_id == "acc_refresh_singleflight"
    auth_manager_module._clear_refresh_singleflight_state()


@pytest.mark.asyncio
async def test_ensure_fresh_releases_token_refresh_admission_when_repo_factory_enter_fails(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    settings.proxy_token_refresh_limit = 1
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    account = _make_account("acc_refresh_repo_failure")
    account.last_refresh = utcnow().replace(year=utcnow().year - 1)

    class _FailingRepos:
        async def __aenter__(self):
            raise RuntimeError("repo enter failed")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    service._repo_factory = lambda: _FailingRepos()

    with pytest.raises(RuntimeError, match="repo enter failed"):
        await service._ensure_fresh(account, force=True)

    lease = await service._get_work_admission().acquire_token_refresh()
    lease.release()


@pytest.mark.asyncio
async def test_response_create_admission_failure_releases_session_gate(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    settings.proxy_response_create_limit = 1
    settings.proxy_admission_wait_timeout_seconds = 0.05
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_gate_release",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        awaiting_response_created=True,
    )
    response_create_gate = asyncio.Semaphore(1)

    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)

    lease = await service._get_work_admission().acquire_response_create()
    try:
        with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
            await service._acquire_request_state_response_create_admission(
                request_state,
                response_create_gate=response_create_gate,
            )
    finally:
        lease.release()

    exc = _assert_proxy_response_error(exc_info.value)
    assert exc.status_code == 429
    assert _proxy_error_code(exc) == "proxy_overloaded"
    assert response_create_gate.locked() is False
    assert request_state.awaiting_response_created is False
    assert request_state.response_create_gate_acquired is False
    assert request_state.response_create_admission is None


@pytest.mark.asyncio
async def test_response_create_admission_waits_on_session_gate_before_shared_capacity(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    settings.proxy_response_create_limit = 2
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    first_request = proxy_service._WebSocketRequestState(
        request_id="ws_req_first",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
    )
    second_request = proxy_service._WebSocketRequestState(
        request_id="ws_req_second",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
    )
    response_create_gate = asyncio.Semaphore(1)

    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)

    await service._acquire_request_state_response_create_admission(
        first_request,
        response_create_gate=response_create_gate,
    )

    waiting_for_gate = asyncio.Event()

    async def acquire_second_request() -> None:
        waiting_for_gate.set()
        await service._acquire_request_state_response_create_admission(
            second_request,
            response_create_gate=response_create_gate,
        )

    second_task = asyncio.create_task(acquire_second_request())
    await waiting_for_gate.wait()
    await asyncio.sleep(0)

    shared_lease = await service._get_work_admission().acquire_response_create()
    shared_lease.release()

    proxy_service._release_websocket_response_create_gate(first_request, response_create_gate)
    await second_task
    proxy_service._release_websocket_response_create_gate(second_request, response_create_gate)

    assert second_request.response_create_gate_acquired is False
    assert second_request.response_create_admission is None


@pytest.mark.asyncio
async def test_compact_selection_budget_exhaustion_returns_upstream_unavailable(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service,
        "_select_account_with_budget",
        AsyncMock(side_effect=proxy_module.ProxyResponseError(502, openai_error("upstream_unavailable", "late"))),
    )

    payload = ResponsesCompactRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": []})

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service.compact_responses(payload, {"session_id": "sid-compact"})

    exc = _assert_proxy_response_error(exc_info.value)
    assert exc.status_code == 502
    assert _proxy_error_code(exc) == "upstream_unavailable"
    assert request_logs.calls[0]["error_code"] == "upstream_unavailable"


@pytest.mark.asyncio
async def test_select_account_with_budget_times_out_during_settings_fetch(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    select_account = AsyncMock(return_value=AccountSelection(account=_make_account("acc_budget"), error_message=None))

    class _SlowSettingsCache:
        async def get(self) -> object:
            await anyio.sleep(0.05)
            return settings

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SlowSettingsCache())
    monkeypatch.setattr(proxy_service, "_remaining_budget_seconds", lambda _deadline: 0.01)
    monkeypatch.setattr(service._load_balancer, "select_account", select_account)

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service._select_account_with_budget(deadline=123.0, request_id="req-budget", kind="compact")

    exc = _assert_proxy_response_error(exc_info.value)
    assert exc.status_code == 502
    assert _proxy_error_code(exc) == "upstream_unavailable"
    assert _proxy_error_message(exc) == "Proxy request budget exhausted"
    select_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_transcribe_budget_exhaustion_blocks_401_retry(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_transcribe_budget")
    transcribe_calls = 0

    runtime_values = dict(settings.__dict__)
    runtime_values["transcription_request_budget_seconds"] = 1.0
    runtime_settings = SimpleNamespace(**runtime_values)
    monotonic_calls = {"count": 0}

    def fake_monotonic():
        monotonic_calls["count"] += 1
        return 100.0 if monotonic_calls["count"] < 7 else 102.0

    async def fake_transcribe(
        audio_bytes: bytes,
        *,
        filename: str,
        content_type: str | None,
        prompt: str | None,
        headers,
        access_token: str,
        account_id: str | None,
        base_url=None,
        session=None,
    ):
        nonlocal transcribe_calls
        transcribe_calls += 1
        raise proxy_module.ProxyResponseError(401, openai_error("invalid_api_key", "token expired"))

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: runtime_settings)
    monkeypatch.setattr(proxy_service.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))
    monkeypatch.setattr(proxy_service, "core_transcribe_audio", fake_transcribe)

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service.transcribe(
            audio_bytes=b"\x01\x02",
            filename="sample.wav",
            content_type="audio/wav",
            prompt=None,
            headers={"session_id": "sid-transcribe"},
        )

    exc = _assert_proxy_response_error(exc_info.value)
    assert exc.status_code == 502
    assert _proxy_error_code(exc) == "upstream_unavailable"
    assert transcribe_calls == 1
    assert request_logs.calls[0]["error_code"] == "upstream_unavailable"
    assert request_logs.calls[0]["transport"] == "http"


@pytest.mark.asyncio
async def test_transcribe_selection_budget_exhaustion_returns_upstream_unavailable(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service,
        "_select_account_with_budget",
        AsyncMock(side_effect=proxy_module.ProxyResponseError(502, openai_error("upstream_unavailable", "late"))),
    )

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service.transcribe(
            audio_bytes=b"\x01\x02",
            filename="sample.wav",
            content_type="audio/wav",
            prompt=None,
            headers={"session_id": "sid-transcribe"},
        )

    exc = _assert_proxy_response_error(exc_info.value)
    assert exc.status_code == 502
    assert _proxy_error_code(exc) == "upstream_unavailable"
    assert request_logs.calls[0]["error_code"] == "upstream_unavailable"


@pytest.mark.asyncio
async def test_transcribe_records_transient_error_for_generic_upstream_failure(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("acc_transcribe_error")
    record_error = AsyncMock()
    record_success = AsyncMock()

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service._load_balancer, "record_error", record_error)
    monkeypatch.setattr(service._load_balancer, "record_success", record_success)
    monkeypatch.setattr(service, "_ensure_fresh", AsyncMock(return_value=account))

    async def failing_transcribe(
        audio_bytes: bytes,
        *,
        filename: str,
        content_type: str | None,
        prompt: str | None,
        headers,
        access_token: str,
        account_id: str | None,
        base_url=None,
        session=None,
    ):
        raise proxy_module.ProxyResponseError(502, openai_error("upstream_unavailable", "late"))

    monkeypatch.setattr(proxy_service, "core_transcribe_audio", failing_transcribe)

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service.transcribe(
            audio_bytes=b"\x01\x02",
            filename="sample.wav",
            content_type="audio/wav",
            prompt=None,
            headers={"session_id": "sid-transcribe"},
        )

    exc = _assert_proxy_response_error(exc_info.value)
    assert exc.status_code == 502
    assert _proxy_error_code(exc) == "upstream_unavailable"
    record_error.assert_awaited_once_with(account)
    record_success.assert_not_awaited()


@pytest.mark.asyncio
async def test_compact_responses_propagates_selection_error_code(monkeypatch):
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        service._load_balancer,
        "select_account",
        AsyncMock(
            return_value=AccountSelection(
                account=None,
                error_message="No accounts with available additional quota for model 'gpt-5.3-codex-spark'",
                error_code="no_additional_quota_eligible_accounts",
            )
        ),
    )

    payload = ResponsesCompactRequest.model_validate(
        {
            "model": "gpt-5.3-codex-spark",
            "instructions": "summarize",
            "input": [],
        }
    )

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service.compact_responses(payload, {"session_id": "sid-compact"})

    exc = _assert_proxy_response_error(exc_info.value)
    assert exc.status_code == 503
    assert _proxy_error_code(exc) == "no_additional_quota_eligible_accounts"
    assert request_logs.calls[0]["error_code"] == "no_additional_quota_eligible_accounts"


def test_settings_parses_image_inline_allowlist_from_csv(monkeypatch):
    monkeypatch.setenv("CODEX_LB_IMAGE_INLINE_ALLOWED_HOSTS", "a.example, b.example ,,C.Example")
    from app.core.config.settings import Settings

    settings = Settings()

    assert settings.image_inline_allowed_hosts == ["a.example", "b.example", "c.example"]


@pytest.mark.asyncio
async def test_transcribe_audio_strips_content_type_case_insensitively():
    response = _TranscribeResponse({"text": "ok"})
    session = _TranscribeSession(response)

    result = await proxy_module.transcribe_audio(
        b"\x01\x02",
        filename="sample.wav",
        content_type="audio/wav",
        prompt="hello",
        headers={
            "content-type": "multipart/form-data; boundary=legacy",
            "X-Request-Id": "req_transcribe_1",
        },
        access_token="token-1",
        account_id="acc_transcribe_1",
        base_url="https://upstream.example",
        session=cast(proxy_module.aiohttp.ClientSession, session),
    )

    assert result == {"text": "ok"}
    assert session.calls
    raw_headers = session.calls[0]["headers"]
    assert isinstance(raw_headers, dict)
    sent_headers = cast(dict[str, str], raw_headers)
    assert all(name.lower() != "content-type" for name in sent_headers)
    assert sent_headers["Authorization"] == "Bearer token-1"
    assert sent_headers["chatgpt-account-id"] == "acc_transcribe_1"


@pytest.mark.asyncio
async def test_transcribe_audio_wraps_timeout_as_upstream_unavailable():
    session = _TimeoutTranscribeSession()

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await proxy_module.transcribe_audio(
            b"\x01\x02",
            filename="sample.wav",
            content_type="audio/wav",
            prompt=None,
            headers={"X-Request-Id": "req_transcribe_timeout"},
            access_token="token-1",
            account_id="acc_transcribe_1",
            base_url="https://upstream.example",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )

    exc = _assert_proxy_response_error(exc_info.value)
    assert exc.status_code == 502
    assert _proxy_error_code(exc) == "upstream_unavailable"
    assert _proxy_error_message(exc) == "Request to upstream timed out"


@pytest.mark.asyncio
async def test_transcribe_audio_honors_timeout_overrides():
    response = _TranscribeResponse({"text": "ok"})
    session = _TranscribeSession(response)

    tokens = proxy_module.push_transcribe_timeout_overrides(connect_timeout_seconds=4.0, total_timeout_seconds=12.0)
    try:
        result = await proxy_module.transcribe_audio(
            b"\x01\x02",
            filename="sample.wav",
            content_type="audio/wav",
            prompt=None,
            headers={"X-Request-Id": "req_transcribe_override"},
            access_token="token-1",
            account_id="acc_transcribe_1",
            base_url="https://upstream.example",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )
    finally:
        proxy_module.pop_transcribe_timeout_overrides(tokens)

    assert result == {"text": "ok"}
    timeout = session.calls[0]["timeout"]
    assert isinstance(timeout, proxy_module.aiohttp.ClientTimeout)
    assert timeout.total == pytest.approx(12.0)
    assert timeout.sock_connect == pytest.approx(4.0)
    assert timeout.sock_read == pytest.approx(12.0)


@pytest.mark.asyncio
async def test_transcribe_audio_uses_configured_budget_when_no_override(monkeypatch):
    class Settings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_connect_timeout_seconds = 5.0
        transcription_request_budget_seconds = 240.0
        log_upstream_request_payload = False

    response = _TranscribeResponse({"text": "ok"})
    session = _TranscribeSession(response)

    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings())
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: None)

    result = await proxy_module.transcribe_audio(
        b"\x01\x02",
        filename="sample.wav",
        content_type="audio/wav",
        prompt=None,
        headers={"X-Request-Id": "req_transcribe_budget"},
        access_token="token-1",
        account_id="acc_transcribe_1",
        base_url="https://upstream.example",
        session=cast(proxy_module.aiohttp.ClientSession, session),
    )

    assert result == {"text": "ok"}
    timeout = session.calls[0]["timeout"]
    assert isinstance(timeout, proxy_module.aiohttp.ClientTimeout)
    assert timeout.total == pytest.approx(240.0)
    assert timeout.sock_connect == pytest.approx(5.0)
    assert timeout.sock_read == pytest.approx(240.0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("json_error", "expected_message"),
    [
        (asyncio.TimeoutError(), "Request to upstream timed out"),
        (proxy_module.aiohttp.ClientPayloadError("payload read failed"), "payload read failed"),
    ],
)
async def test_transcribe_audio_maps_body_read_transport_errors_to_upstream_unavailable(
    json_error: Exception,
    expected_message: str,
):
    response = _TranscribeResponse({"text": "ignored"}, json_error=json_error)
    session = _TranscribeSession(response)

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await proxy_module.transcribe_audio(
            b"\x01\x02",
            filename="sample.wav",
            content_type="audio/wav",
            prompt=None,
            headers={"X-Request-Id": "req_transcribe_body_read"},
            access_token="token-1",
            account_id="acc_transcribe_1",
            base_url="https://upstream.example",
            session=cast(proxy_module.aiohttp.ClientSession, session),
        )

    exc = _assert_proxy_response_error(exc_info.value)
    assert exc.status_code == 502
    assert _proxy_error_code(exc) == "upstream_unavailable"
    assert _proxy_error_message(exc) == expected_message


class _CBStub:
    def __init__(self) -> None:
        self.failures: list[Exception] = []
        self.successes: int = 0

    async def pre_call_check(self) -> bool:
        return False

    async def release_half_open_probe(self) -> None:
        pass

    async def _record_failure(self, exc: Exception) -> None:
        self.failures.append(exc)

    async def _record_success(self) -> None:
        self.successes += 1


@pytest.mark.asyncio
async def test_cb_context_normal_200_records_success(monkeypatch):
    cb = _CBStub()
    monkeypatch.setattr(proxy_module, "get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=True))
    monkeypatch.setattr(proxy_module, "get_circuit_breaker_for_account", lambda _aid, _s: cb)

    resp = SimpleNamespace(status=200)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_cm():
        yield resp

    async with proxy_module._service_circuit_breaker_context(_fake_cm(), account_id="acc_test") as r:
        assert r.status == 200

    assert cb.successes == 1
    assert cb.failures == []


@pytest.mark.asyncio
async def test_cb_context_4xx_caller_raises_records_success(monkeypatch):
    cb = _CBStub()
    monkeypatch.setattr(proxy_module, "get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=True))
    monkeypatch.setattr(proxy_module, "get_circuit_breaker_for_account", lambda _aid, _s: cb)

    resp = SimpleNamespace(status=429)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_cm():
        yield resp

    class _ClientError(Exception):
        status_code = 429

    with pytest.raises(_ClientError):
        async with proxy_module._service_circuit_breaker_context(_fake_cm(), account_id="acc_test"):
            raise _ClientError("rate limited")

    assert cb.successes == 1
    assert cb.failures == []


@pytest.mark.asyncio
async def test_cb_context_200_body_timeout_records_failure(monkeypatch):
    cb = _CBStub()
    monkeypatch.setattr(proxy_module, "get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=True))
    monkeypatch.setattr(proxy_module, "get_circuit_breaker_for_account", lambda _aid, _s: cb)

    resp = SimpleNamespace(status=200)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_cm():
        yield resp

    with pytest.raises(asyncio.TimeoutError):
        async with proxy_module._service_circuit_breaker_context(_fake_cm(), account_id="acc_test"):
            raise asyncio.TimeoutError("body read timeout")

    assert cb.successes == 0
    assert len(cb.failures) == 1


@pytest.mark.asyncio
async def test_cb_context_connection_failure_records_failure(monkeypatch):
    cb = _CBStub()
    monkeypatch.setattr(proxy_module, "get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=True))
    monkeypatch.setattr(proxy_module, "get_circuit_breaker_for_account", lambda _aid, _s: cb)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_cm():
        raise ConnectionError("upstream unreachable")
        yield  # pragma: no cover

    with pytest.raises(ConnectionError):
        async with proxy_module._service_circuit_breaker_context(_fake_cm(), account_id="acc_test"):
            pass  # pragma: no cover

    assert cb.successes == 0
    assert len(cb.failures) == 1


@pytest.mark.asyncio
async def test_cb_context_open_circuit_closes_request_context_manager(monkeypatch):
    class _OpenCircuitCB:
        async def pre_call_check(self) -> bool:
            raise proxy_module.CircuitBreakerOpenError("open")

        async def release_half_open_probe(self) -> None:
            pass

        async def _record_failure(self, exc: Exception) -> None:
            del exc

        async def _record_success(self) -> None:
            pass

    class _RequestCM:
        def __init__(self) -> None:
            self.close_called = False

        def close(self) -> None:
            self.close_called = True

        async def __aenter__(self):
            raise AssertionError("context manager should not be entered")

        async def __aexit__(self, exc_type, exc, tb) -> None:
            del exc_type, exc, tb

    cb = _OpenCircuitCB()
    cm = _RequestCM()
    monkeypatch.setattr(proxy_module, "get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=True))
    monkeypatch.setattr(proxy_module, "get_circuit_breaker_for_account", lambda _aid, _s: cb)

    with pytest.raises(proxy_module.CircuitBreakerOpenError):
        async with proxy_module._service_circuit_breaker_context(cm, account_id="acc_test"):
            pass  # pragma: no cover

    assert cm.close_called is True


@pytest.mark.asyncio
async def test_lookup_file_pin_returns_live_entry_and_evicts_expired(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    fake_now = [100.0]

    monkeypatch.setattr(proxy_service.time, "monotonic", lambda: fake_now[0])

    await service._pin_file_account("file_live", "acc_live")

    entry = await service._lookup_file_pin("file_live")

    assert entry is not None
    assert entry.account_id == "acc_live"

    fake_now[0] += service._FILE_ACCOUNT_PIN_TTL_SECONDS + 1

    expired = await service._lookup_file_pin("file_live")

    assert expired is None


@pytest.mark.asyncio
async def test_stream_http_bridge_or_retry_rejects_input_image_file_id(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        proxy_service,
        "_http_bridge_runtime_config",
        lambda _dashboard_settings, _app_settings: proxy_service._HTTPBridgeRuntimeConfig(
            enabled=False,
            idle_ttl_seconds=30.0,
            codex_idle_ttl_seconds=30.0,
            max_sessions=8,
            queue_limit=16,
            prompt_cache_idle_ttl_seconds=30.0,
            gateway_safe_mode=False,
        ),
    )
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"type": "input_image", "file_id": "file_pic"}],
        }
    )

    with pytest.raises(proxy_module.ProxyResponseError) as info:
        async for _ in service._stream_http_bridge_or_retry(
            payload=payload,
            headers={},
            codex_session_affinity=False,
            propagate_http_errors=False,
            openai_cache_affinity=False,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
        ):
            pass

    assert info.value.status_code == 400
    assert _proxy_error_code(info.value) == "unsupported_input_image_format"
    assert "data: URLs" in (_proxy_error_message(info.value) or "")


@pytest.mark.asyncio
async def test_stream_http_bridge_or_retry_rejects_input_image_sediment_url(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        proxy_service,
        "_http_bridge_runtime_config",
        lambda _dashboard_settings, _app_settings: proxy_service._HTTPBridgeRuntimeConfig(
            enabled=False,
            idle_ttl_seconds=30.0,
            codex_idle_ttl_seconds=30.0,
            max_sessions=8,
            queue_limit=16,
            prompt_cache_idle_ttl_seconds=30.0,
            gateway_safe_mode=False,
        ),
    )
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"type": "input_image", "image_url": "sediment://file_pic"}],
        }
    )

    with pytest.raises(proxy_module.ProxyResponseError) as info:
        async for _ in service._stream_http_bridge_or_retry(
            payload=payload,
            headers={},
            codex_session_affinity=False,
            propagate_http_errors=False,
            openai_cache_affinity=False,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
        ):
            pass

    assert info.value.status_code == 400
    assert _proxy_error_code(info.value) == "unsupported_input_image_format"


@pytest.mark.asyncio
async def test_stream_http_bridge_or_retry_routes_input_file_file_id_without_rejecting(monkeypatch):
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    settings = _make_proxy_settings(log_proxy_service_tier_trace=False)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        proxy_service,
        "_http_bridge_runtime_config",
        lambda _dashboard_settings, _app_settings: proxy_service._HTTPBridgeRuntimeConfig(
            enabled=False,
            idle_ttl_seconds=30.0,
            codex_idle_ttl_seconds=30.0,
            max_sessions=8,
            queue_limit=16,
            prompt_cache_idle_ttl_seconds=30.0,
            gateway_safe_mode=False,
        ),
    )
    await service._pin_file_account("file_doc", "acc_doc")
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"type": "input_file", "file_id": "file_doc"}],
        }
    )

    calls: list[tuple[object, str | None]] = []

    async def fake_stream_with_retry(
        payload,
        headers,
        *,
        rewritten_file_account_id: str | None = None,
        **kwargs,
    ):
        del headers, kwargs
        calls.append((payload, rewritten_file_account_id))
        yield "data: retry\n\n"

    monkeypatch.setattr(service, "_stream_with_retry", fake_stream_with_retry)

    output = [
        line
        async for line in service._stream_http_bridge_or_retry(
            payload=payload,
            headers={},
            codex_session_affinity=False,
            propagate_http_errors=False,
            openai_cache_affinity=False,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
        )
    ]

    assert output == ["data: retry\n\n"]
    assert calls == [(payload, "acc_doc")]


def test_classify_upstream_close_rejected_only_for_clean_close_before_any_response_event():
    assert proxy_service._classify_upstream_close(1000, response_events_seen=0) == "rejected"
    assert proxy_service._classify_upstream_close(1000, response_events_seen=1) == "transient"
    assert proxy_service._classify_upstream_close(1011, response_events_seen=0) == "transient"


@pytest.mark.asyncio
async def test_retry_http_bridge_precreated_request_suppresses_retry_for_rejected_close():
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    request_state = proxy_service._WebSocketRequestState(
        request_id="req_bridge",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        awaiting_response_created=True,
        request_text='{"type":"response.create"}',
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("prompt_cache", "bridge-key", None),
        headers={},
        affinity=proxy_service._AffinityPolicy(),
        request_model="gpt-5.1",
        account=_make_account("acc_bridge"),
        upstream=AsyncMock(),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([request_state]),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=1,
        last_used_at=0.0,
        idle_ttl_seconds=30.0,
        last_upstream_close_code=1000,
    )

    retried = await service._retry_http_bridge_precreated_request(session)

    assert retried is False
    assert request_state.error_code_override == "upstream_rejected_input"
    assert request_state.error_http_status_override == 502
    assert "close_code=1000" in (request_state.error_message_override or "")
