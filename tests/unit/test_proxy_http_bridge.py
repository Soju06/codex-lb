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
