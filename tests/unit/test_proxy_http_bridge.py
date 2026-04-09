from __future__ import annotations

import asyncio
from collections import deque
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio
import pytest

from app.core.clients.proxy import ProxyResponseError
from app.core.clients.proxy_websocket import UpstreamResponsesWebSocket
from app.core.config.settings import Settings
from app.db.models import AccountStatus
from app.modules.proxy import service as proxy_service

pytestmark = pytest.mark.unit


def _make_app_settings(*, bridge_enabled: bool = True) -> Settings:
    return Settings(http_responses_session_bridge_enabled=bridge_enabled)


def _make_api_key(
    *,
    key_id: str,
    assigned_account_ids: list[str],
    account_assignment_scope_enabled: bool | None = None,
) -> proxy_service.ApiKeyData:
    return proxy_service.ApiKeyData(
        id=key_id,
        name="bridge-key",
        key_prefix="sk-bridge",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        last_used_at=None,
        account_assignment_scope_enabled=(
            bool(assigned_account_ids) if account_assignment_scope_enabled is None else account_assignment_scope_enabled
        ),
        assigned_account_ids=assigned_account_ids,
    )


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_reuses_live_local_session_without_ring_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("prompt_cache_key", "bridge-key", None)
    existing = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key"),
        request_model="gpt-5.4-mini",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace()),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    service._http_bridge_sessions[key] = existing
    monkeypatch.setattr(
        service,
        "_prune_http_bridge_sessions_locked",
        AsyncMock(),
    )
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(),
    )

    async def _unexpected_owner_lookup(*args: object, **kwargs: object) -> str:
        raise AssertionError("live local session reuse must not hit the ring")

    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", _unexpected_owner_lookup)
    monkeypatch.setattr(proxy_service, "_active_http_bridge_instance_ring", _unexpected_owner_lookup)

    reused = await service._get_or_create_http_bridge_session(
        key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key"),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
    )

    assert reused is existing
    assert reused.request_model == "gpt-5.4"
    assert reused.last_used_at > 1.0


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_replaces_live_session_when_account_is_no_longer_assigned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("request", "bridge-key", "key-1")
    stale_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key"),
        request_model="gpt-5.4-mini",
        account=cast(Any, SimpleNamespace(id="acc-stale", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    replacement_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key"),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-fresh", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    service._http_bridge_sessions[key] = stale_session
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", AsyncMock())
    monkeypatch.setattr(
        service,
        "_create_http_bridge_session",
        AsyncMock(return_value=replacement_session),
    )
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(),
    )
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a"])),
    )
    close_session = AsyncMock()
    monkeypatch.setattr(service, "_close_http_bridge_session", close_session)

    reused = await service._get_or_create_http_bridge_session(
        key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key"),
        api_key=_make_api_key(key_id="key-1", assigned_account_ids=["acc-fresh"]),
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
    )

    assert reused is replacement_session
    assert service._http_bridge_sessions[key] is replacement_session
    assert stale_session.closed is True
    assert any(call.args == (stale_session,) for call in close_session.await_args_list)


def test_http_bridge_session_key_infers_strength_from_affinity_kind() -> None:
    assert proxy_service._HTTPBridgeSessionKey("turn_state_header", "turn", None).strength == "hard"
    assert proxy_service._HTTPBridgeSessionKey("session_header", "session", None).strength == "hard"
    assert proxy_service._HTTPBridgeSessionKey("prompt_cache", "cache", None).strength == "soft"
    assert proxy_service._HTTPBridgeSessionKey("request", "request", None).strength == "soft"


def test_http_bridge_owner_check_required_keeps_prompt_cache_soft() -> None:
    key = proxy_service._HTTPBridgeSessionKey("prompt_cache", "cache", None)

    assert proxy_service._http_bridge_owner_check_required(key, gateway_safe_mode=False) is False
    assert proxy_service._http_bridge_owner_check_required(key, gateway_safe_mode=True) is False


def test_headers_with_authorization_restores_missing_proxy_api_header() -> None:
    headers = proxy_service._headers_with_authorization({"x-request-id": "req-1"}, "Bearer proxy-key")

    assert headers["Authorization"] == "Bearer proxy-key"
    assert headers["x-request-id"] == "req-1"


def test_headers_with_authorization_does_not_override_existing_value() -> None:
    headers = proxy_service._headers_with_authorization({"authorization": "Bearer existing"}, "Bearer proxy-key")

    assert headers["authorization"] == "Bearer existing"


def test_make_http_bridge_session_key_prefers_signed_forwarded_affinity_over_generated_turn_state() -> None:
    payload = proxy_service.ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "hi"})

    key = proxy_service._make_http_bridge_session_key(
        payload,
        headers={
            "x-codex-turn-state": "http_turn_generated",
            "x-codex-bridge-affinity-kind": "session_header",
            "x-codex-bridge-affinity-key": "sid-123",
        },
        affinity=proxy_service._AffinityPolicy(key="sid-123"),
        api_key=None,
        request_id="req-1",
        allow_forwarded_affinity_headers=True,
    )

    assert key.affinity_kind == "session_header"
    assert key.affinity_key == "sid-123"
    assert key.strength == "hard"


def test_make_http_bridge_session_key_ignores_forwarded_affinity_headers_on_public_requests() -> None:
    payload = proxy_service.ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "hi"})

    key = proxy_service._make_http_bridge_session_key(
        payload,
        headers={
            "x-codex-bridge-affinity-kind": "session_header",
            "x-codex-bridge-affinity-key": "sid-123",
        },
        affinity=proxy_service._AffinityPolicy(key="cache-123", kind=proxy_service.StickySessionKind.PROMPT_CACHE),
        api_key=None,
        request_id="req-1",
        allow_forwarded_affinity_headers=False,
    )

    assert key.affinity_kind == "prompt_cache"
    assert key.affinity_key == "cache-123"
    assert key.strength == "soft"


def test_http_bridge_requires_cluster_registration_for_non_loopback_advertise_url() -> None:
    settings = Settings(
        http_responses_session_bridge_instance_id="instance-a",
        http_responses_session_bridge_advertise_base_url="http://instance-a.codex-lb-bridge.default.svc.cluster.local:2455",
    )

    assert proxy_service._http_bridge_requires_cluster_registration(settings) is True


def test_http_bridge_requires_cluster_registration_skips_loopback_single_replica() -> None:
    settings = Settings(http_responses_session_bridge_advertise_base_url="http://127.0.0.1:2455")

    assert proxy_service._http_bridge_requires_cluster_registration(settings) is False


@pytest.mark.asyncio
async def test_http_bridge_waits_for_registration_for_hard_keys_before_startup_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.core.startup as startup_module

    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    settings = Settings(
        http_responses_session_bridge_instance_id="instance-a",
        http_responses_session_bridge_advertise_base_url="http://instance-a.bridge.default.svc.cluster.local:2455",
    )
    monkeypatch.setattr(startup_module, "_startup_complete", False)
    monkeypatch.setattr(startup_module, "_bridge_registration_complete", False)

    assert (
        await proxy_service._http_bridge_should_wait_for_registration(
            service,
            proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
            settings,
        )
        is True
    )


@pytest.mark.asyncio
async def test_forward_http_bridge_request_to_owner_preserves_session_header_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    owner_forward = proxy_service._HTTPBridgeOwnerForward(
        owner_instance="instance-b",
        owner_endpoint="http://instance-b",
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
    )
    payload = proxy_service.ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "hi"})
    captured: dict[str, object] = {}

    async def fake_stream_responses(**kwargs: object):
        captured.update(kwargs)
        if False:
            yield ""
        return

    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        service,
        "_http_bridge_owner_client",
        cast(Any, SimpleNamespace(stream_responses=fake_stream_responses)),
    )

    chunks = [
        chunk
        async for chunk in service._forward_http_bridge_request_to_owner(
            owner_forward=owner_forward,
            payload=payload,
            headers={"x-codex-session-id": "sid-123"},
            api_key_reservation=None,
            codex_session_affinity=True,
            downstream_turn_state="http_turn_generated",
            request_started_at=10.0,
            proxy_api_authorization=None,
        )
    ]

    assert chunks == []
    context = cast(proxy_service.HTTPBridgeForwardContext, captured["context"])
    assert context.downstream_turn_state == "http_turn_generated"
    assert context.original_affinity_kind == "session_header"
    assert context.original_affinity_key == "sid-123"
    assert cast(dict[str, str], captured["headers"])["x-codex-session-id"] == "sid-123"


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_returns_owner_forward_for_hard_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_123", None)
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", AsyncMock())
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(),
    )
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-b"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )
    service._ring_membership = cast(Any, SimpleNamespace(resolve_endpoint=AsyncMock(return_value="http://instance-b")))

    resolved = await service._get_or_create_http_bridge_session(
        key,
        headers={"x-codex-turn-state": "http_turn_123"},
        affinity=proxy_service._AffinityPolicy(key="http_turn_123"),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        allow_forward_to_owner=True,
    )

    assert isinstance(resolved, proxy_service._HTTPBridgeOwnerForward)
    assert resolved.owner_instance == "instance-b"
    assert resolved.owner_endpoint == "http://instance-b"
    assert resolved.key == key


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_preserves_explicit_forwarded_affinity_on_missing_turn_state_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None)
    created_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    captured: dict[str, object] = {}

    async def fake_create_http_bridge_session(
        create_key: proxy_service._HTTPBridgeSessionKey,
        *,
        headers: dict[str, str],
        affinity: proxy_service._AffinityPolicy,
        api_key: proxy_service.ApiKeyData | None,
        request_model: str | None,
        idle_ttl_seconds: float,
    ) -> proxy_service._HTTPBridgeSession:
        captured["key"] = create_key
        return created_session

    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", AsyncMock())
    monkeypatch.setattr(service, "_create_http_bridge_session", fake_create_http_bridge_session)
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )

    resolved = await service._get_or_create_http_bridge_session(
        key,
        headers={"x-codex-turn-state": "http_turn_generated"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        forwarded_request=True,
        forwarded_affinity_kind="session_header",
        forwarded_affinity_key="sid-123",
    )

    assert resolved is created_session
    assert captured["key"] == key


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_falls_back_to_session_header_when_turn_state_alias_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    requested_key = proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_generated", None)
    fallback_key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None)
    created_session = proxy_service._HTTPBridgeSession(
        key=fallback_key,
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    captured: dict[str, object] = {}

    async def fake_create_http_bridge_session(
        create_key: proxy_service._HTTPBridgeSessionKey,
        *,
        headers: dict[str, str],
        affinity: proxy_service._AffinityPolicy,
        api_key: proxy_service.ApiKeyData | None,
        request_model: str | None,
        idle_ttl_seconds: float,
    ) -> proxy_service._HTTPBridgeSession:
        captured["key"] = create_key
        return created_session

    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", AsyncMock())
    monkeypatch.setattr(service, "_create_http_bridge_session", fake_create_http_bridge_session)
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )

    resolved = await service._get_or_create_http_bridge_session(
        requested_key,
        headers={
            "x-codex-turn-state": "http_turn_generated",
            "x-codex-session-id": "sid-123",
        },
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        previous_response_id="resp_prev_1",
    )

    assert resolved is created_session
    assert captured["key"] == fallback_key


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_recovers_from_previous_response_id_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_missing_alias", None)
    recovered_key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None)
    recovered_session = proxy_service._HTTPBridgeSession(
        key=recovered_key,
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
        previous_response_ids={"resp_prev_1"},
    )
    service._http_bridge_sessions[recovered_key] = recovered_session
    service._http_bridge_previous_response_index[
        proxy_service._http_bridge_previous_response_alias_key("resp_prev_1", None)
    ] = recovered_key
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", AsyncMock())
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )

    resolved = await service._get_or_create_http_bridge_session(
        key,
        headers={"x-codex-turn-state": "http_turn_missing_alias"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        previous_response_id="resp_prev_1",
    )

    assert resolved is recovered_session
    assert "http_turn_missing_alias" in recovered_session.downstream_turn_state_aliases


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_allows_local_rebind_for_previous_response_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None)
    created_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", AsyncMock())
    monkeypatch.setattr(service, "_create_http_bridge_session", AsyncMock(return_value=created_session))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-b"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )

    resolved = await service._get_or_create_http_bridge_session(
        key,
        headers={"x-codex-session-id": "sid-123"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-123",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        previous_response_id="resp_prev_1",
        allow_previous_response_recovery_rebind=True,
    )

    assert resolved is created_session


@pytest.mark.asyncio
async def test_should_attempt_local_bootstrap_rebind_for_session_header_without_turn_state() -> None:
    exc = ProxyResponseError(
        503,
        {"error": {"code": "bridge_owner_unreachable", "message": "owner down", "type": "server_error"}},
    )

    assert (
        proxy_service._http_bridge_should_attempt_local_bootstrap_rebind(
            exc,
            key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
            headers={"x-codex-session-id": "sid-123"},
            previous_response_id=None,
        )
        is True
    )

    assert (
        proxy_service._http_bridge_should_attempt_local_bootstrap_rebind(
            exc,
            key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-123", None),
            headers={"x-codex-session-id": "sid-123", "x-codex-turn-state": "http_turn_123"},
            previous_response_id=None,
        )
        is False
    )


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_falls_back_to_retry_when_owner_endpoint_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_123", None)
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", AsyncMock())
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-b"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )
    service._ring_membership = cast(Any, SimpleNamespace(resolve_endpoint=AsyncMock(return_value=None)))

    with pytest.raises(ProxyResponseError) as exc_info:
        await service._get_or_create_http_bridge_session(
            key,
            headers={"x-codex-turn-state": "http_turn_123"},
            affinity=proxy_service._AffinityPolicy(key="http_turn_123"),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            max_sessions=8,
            allow_forward_to_owner=True,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.payload["error"]["code"] == "bridge_instance_mismatch"


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_soft_mismatch_rebinds_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("prompt_cache", "cache-key", None)
    created_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="cache-key"),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-fresh", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", AsyncMock())
    monkeypatch.setattr(service, "_create_http_bridge_session", AsyncMock(return_value=created_session))
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(),
    )
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-b"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )

    resolved = await service._get_or_create_http_bridge_session(
        key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="cache-key"),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        allow_forward_to_owner=True,
        gateway_safe_mode=True,
    )

    assert resolved is created_session


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_prompt_cache_mismatch_stays_local_when_gateway_safe_mode_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("prompt_cache", "cache-key", None)
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", AsyncMock())
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-b"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )
    created_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="cache-key"),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-fresh", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    monkeypatch.setattr(service, "_create_http_bridge_session", AsyncMock(return_value=created_session))

    resolved = await service._get_or_create_http_bridge_session(
        key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="cache-key"),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        allow_forward_to_owner=True,
        gateway_safe_mode=False,
    )

    assert resolved is created_session


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_prevents_forward_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_123", None)
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", AsyncMock())
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(),
    )
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-b"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a", "instance-b"])),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await service._get_or_create_http_bridge_session(
            key,
            headers={"x-codex-turn-state": "http_turn_123"},
            affinity=proxy_service._AffinityPolicy(key="http_turn_123"),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            max_sessions=8,
            allow_forward_to_owner=True,
            forwarded_request=True,
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.payload["error"]["code"] == "bridge_forward_loop_prevented"


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_replaces_live_session_when_scope_becomes_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("request", "bridge-key", "key-1")
    stale_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key"),
        request_model="gpt-5.4-mini",
        account=cast(Any, SimpleNamespace(id="acc-stale", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    replacement_session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key"),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-fresh", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=2.0,
        idle_ttl_seconds=120.0,
    )
    service._http_bridge_sessions[key] = stale_session
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", AsyncMock())
    monkeypatch.setattr(
        service,
        "_create_http_bridge_session",
        AsyncMock(return_value=replacement_session),
    )
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: _make_app_settings(),
    )
    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", AsyncMock(return_value="instance-a"))
    monkeypatch.setattr(
        proxy_service,
        "_active_http_bridge_instance_ring",
        AsyncMock(return_value=("instance-a", ["instance-a"])),
    )
    close_session = AsyncMock()
    monkeypatch.setattr(service, "_close_http_bridge_session", close_session)

    reused = await service._get_or_create_http_bridge_session(
        key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="bridge-key"),
        api_key=_make_api_key(
            key_id="key-1",
            assigned_account_ids=[],
            account_assignment_scope_enabled=True,
        ),
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
    )

    assert reused is replacement_session
    assert service._http_bridge_sessions[key] is replacement_session
    assert stale_session.closed is True
    assert any(call.args == (stale_session,) for call in close_session.await_args_list)
