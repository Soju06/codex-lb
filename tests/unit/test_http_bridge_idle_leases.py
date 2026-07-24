"""Idle HTTP bridge sessions release their account stream lease between turns."""

from __future__ import annotations

import asyncio
from collections import deque
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio
import pytest

from app.core.clients.proxy import ProxyResponseError
from app.core.clients.proxy_websocket import UpstreamResponsesWebSocket
from app.db.models import AccountStatus
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.http_bridge import request_submit as http_bridge_request_submit_module

pytestmark = pytest.mark.unit


def _make_bridge_session(*, queued_request_count: int = 0) -> proxy_service._HTTPBridgeSession:
    session_key = proxy_service._HTTPBridgeSessionKey("session_header", "idle-lease-test", None)
    return proxy_service._HTTPBridgeSession(
        key=session_key,
        headers={"x-codex-session-id": "idle-lease-test"},
        affinity=proxy_service._AffinityPolicy(
            key="idle-lease-test",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.2",
        account=cast(Any, SimpleNamespace(id="acc-bridge", status=AccountStatus.ACTIVE, plan_type="plus")),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=queued_request_count,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )


def _make_lease(lease_id: str) -> proxy_service.AccountLease:
    return proxy_service.AccountLease(lease_id=lease_id, account_id="acc-bridge", kind="stream", acquired_at=0.0)


@pytest.mark.asyncio
async def test_idle_session_releases_stream_lease() -> None:
    mixin = http_bridge_request_submit_module._HTTPBridgeRequestSubmitMixin
    session = _make_bridge_session()
    lease = _make_lease("l1")
    session.account_lease = lease
    fake_self = SimpleNamespace(_load_balancer=SimpleNamespace(release_account_lease=AsyncMock()))

    await mixin._maybe_release_idle_http_bridge_session_lease(fake_self, session)

    assert session.account_lease is None
    fake_self._load_balancer.release_account_lease.assert_awaited_once_with(lease)


@pytest.mark.asyncio
async def test_busy_or_closed_session_keeps_stream_lease() -> None:
    mixin = http_bridge_request_submit_module._HTTPBridgeRequestSubmitMixin
    lease = _make_lease("l2")
    fake_self = SimpleNamespace(_load_balancer=SimpleNamespace(release_account_lease=AsyncMock()))

    busy = _make_bridge_session(queued_request_count=1)
    busy.account_lease = lease
    await mixin._maybe_release_idle_http_bridge_session_lease(fake_self, busy)
    assert busy.account_lease is lease

    closed = _make_bridge_session()
    closed.account_lease = lease
    closed.closed = True
    await mixin._maybe_release_idle_http_bridge_session_lease(fake_self, closed)
    assert closed.account_lease is lease

    fake_self._load_balancer.release_account_lease.assert_not_awaited()


@pytest.mark.asyncio
async def test_next_turn_reacquires_stream_lease() -> None:
    mixin = http_bridge_request_submit_module._HTTPBridgeRequestSubmitMixin
    session = _make_bridge_session()
    assert session.account_lease is None
    lease = _make_lease("l3")
    fake_self = SimpleNamespace(_load_balancer=SimpleNamespace(acquire_account_lease=AsyncMock(return_value=lease)))

    async with session.pending_lock:
        await mixin._ensure_http_bridge_session_stream_lease_locked(fake_self, session)

    assert session.account_lease is lease
    fake_self._load_balancer.acquire_account_lease.assert_awaited_once_with("acc-bridge", kind="stream")


@pytest.mark.asyncio
async def test_reacquire_denial_raises_local_cap_envelope() -> None:
    mixin = http_bridge_request_submit_module._HTTPBridgeRequestSubmitMixin
    session = _make_bridge_session()
    fake_self = SimpleNamespace(_load_balancer=SimpleNamespace(acquire_account_lease=AsyncMock(return_value=None)))

    with pytest.raises(ProxyResponseError) as exc_info:
        async with session.pending_lock:
            await mixin._ensure_http_bridge_session_stream_lease_locked(fake_self, session)

    assert exc_info.value.status_code == 429
    assert exc_info.value.payload["error"]["code"] == "account_stream_cap"
    assert session.account_lease is None


@pytest.mark.asyncio
async def test_reacquire_noop_when_lease_already_held() -> None:
    mixin = http_bridge_request_submit_module._HTTPBridgeRequestSubmitMixin
    session = _make_bridge_session()
    lease = _make_lease("l4")
    session.account_lease = lease
    fake_self = SimpleNamespace(_load_balancer=SimpleNamespace(acquire_account_lease=AsyncMock()))

    async with session.pending_lock:
        await mixin._ensure_http_bridge_session_stream_lease_locked(fake_self, session)

    assert session.account_lease is lease
    fake_self._load_balancer.acquire_account_lease.assert_not_awaited()
