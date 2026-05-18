from __future__ import annotations

import asyncio
from collections import deque
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio
import pytest

from app.core.clients.proxy_websocket import UpstreamResponsesWebSocket
from app.db.models import AccountStatus
from app.modules.proxy import service as proxy_service

pytestmark = pytest.mark.unit


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
        lambda: SimpleNamespace(http_responses_session_bridge_enabled=True),
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
async def test_get_or_create_http_bridge_session_closes_stale_session_before_previous_response_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    request_key = proxy_service._HTTPBridgeSessionKey("request", "stale-request", None)
    previous_key = proxy_service._HTTPBridgeSessionKey("prompt_cache_key", "previous-live", None)
    stale = proxy_service._HTTPBridgeSession(
        key=request_key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="stale-request"),
        request_model="gpt-5.4-mini",
        account=cast(Any, SimpleNamespace(id="acc-stale", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace()),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
        closed=True,
    )
    previous = proxy_service._HTTPBridgeSession(
        key=previous_key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="previous-live"),
        request_model="gpt-5.4-mini",
        account=cast(Any, SimpleNamespace(id="acc-live", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace()),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    service._http_bridge_sessions[request_key] = stale
    service._http_bridge_sessions[previous_key] = previous
    service._http_bridge_previous_response_index[
        proxy_service._http_bridge_previous_response_alias_key("resp-live", None)
    ] = previous_key
    close_session = AsyncMock()
    monkeypatch.setattr(service, "_close_http_bridge_session", close_session)
    monkeypatch.setattr(service, "_prune_http_bridge_sessions_locked", AsyncMock())
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: SimpleNamespace(http_responses_session_bridge_enabled=True),
    )

    async def _local_owner(*args: object, **kwargs: object) -> None:
        return None

    async def _local_ring(*args: object, **kwargs: object) -> tuple[None, list[str]]:
        return None, []

    monkeypatch.setattr(proxy_service, "_http_bridge_owner_instance", _local_owner)
    monkeypatch.setattr(proxy_service, "_active_http_bridge_instance_ring", _local_ring)

    reused = await service._get_or_create_http_bridge_session(
        request_key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="stale-request"),
        api_key=None,
        request_model="gpt-5.4",
        idle_ttl_seconds=120.0,
        max_sessions=8,
        previous_response_id="resp-live",
    )

    assert reused is previous
    close_session.assert_awaited_once_with(stale)


@pytest.mark.asyncio
async def test_process_http_bridge_upstream_text_indexes_terminal_response_without_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    key = proxy_service._HTTPBridgeSessionKey("prompt_cache_key", "terminal-only", None)
    session = proxy_service._HTTPBridgeSession(
        key=key,
        headers={},
        affinity=proxy_service._AffinityPolicy(key="terminal-only"),
        request_model="gpt-5.4-mini",
        account=cast(Any, SimpleNamespace(id="acc-live", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace()),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(
            [
                proxy_service._WebSocketRequestState(
                    request_id="req-terminal-only",
                    model="gpt-5.4-mini",
                    service_tier=None,
                    reasoning_effort=None,
                    api_key_reservation=None,
                    started_at=0.0,
                )
            ]
        ),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=1,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    service._http_bridge_sessions[key] = session
    finalize = AsyncMock()
    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize)

    await service._process_http_bridge_upstream_text(
        session,
        '{"type":"response.completed","response":{"id":"resp-terminal-only","status":"completed"}}',
    )

    assert (
        service._http_bridge_previous_response_index[
            proxy_service._http_bridge_previous_response_alias_key("resp-terminal-only", None)
        ]
        == key
    )
    assert session.previous_response_ids == {"resp-terminal-only"}
    finalize.assert_awaited_once()
