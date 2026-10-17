from __future__ import annotations

import asyncio
from collections import deque
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import anyio
import pytest
from fastapi import WebSocket

from app.core.clients.proxy_websocket import UpstreamWebSocket
from app.db.models import Account
from app.modules.proxy._service.support import (
    _DownstreamWebSocketActivity,
    _WebSocketContinuityState,
    _WebSocketUpstreamControl,
)
from app.modules.proxy._service.websocket.mixin import _process_upstream_websocket_transport_end
from app.modules.proxy._service.websocket.protocol import _WebSocketServiceProtocol
from tests.unit.test_websocket_terminal_cancellation import _request_state

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize("newer_anchor", [False, True])
async def test_idle_loss_retires_only_the_physical_upstream_anchor(newer_anchor: bool) -> None:
    control = _WebSocketUpstreamControl(completed_response_delivered=True)
    continuity = _WebSocketContinuityState(
        last_completed_response_id="resp_current",
        last_completed_connection_id="newer_connection" if newer_anchor else control.connection_id,
        last_completed_input_count=2,
        last_pending_function_call_ids=["call_current"],
    )
    socket = SimpleNamespace(close=AsyncMock())
    upstream = SimpleNamespace(close=AsyncMock())
    proxy = SimpleNamespace(_fail_pending_websocket_requests=AsyncMock())
    activity = _DownstreamWebSocketActivity()
    await _process_upstream_websocket_transport_end(
        cast(_WebSocketServiceProtocol, proxy),
        cast(WebSocket, socket),
        cast(UpstreamWebSocket, upstream),
        message=SimpleNamespace(kind="close", close_code=1000),
        account=cast(Account, SimpleNamespace(id="account")),
        account_id_value="account",
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        client_send_lock=anyio.Lock(),
        api_key=None,
        upstream_control=control,
        response_create_gate=asyncio.Semaphore(1),
        downstream_activity=activity,
        continuity_state=continuity,
        codex_session_affinity=True,
    )
    socket.close.assert_awaited_once_with(code=1012, reason="Upstream connection lost; reconnect with full context")
    upstream.close.assert_awaited_once()
    proxy._fail_pending_websocket_requests.assert_not_awaited()
    assert activity.disconnected
    assert continuity.last_completed_response_id == ("resp_current" if newer_anchor else None)
    assert continuity.last_pending_function_call_ids == (["call_current"] if newer_anchor else [])


@pytest.mark.parametrize("boundary", ["public", "not_completed", "pending", "replay"])
async def test_idle_notification_preserves_other_lifecycle_boundaries(boundary: str) -> None:
    control = _WebSocketUpstreamControl(completed_response_delivered=boundary != "not_completed")
    pending = deque()
    if boundary == "pending":
        pending.append(_request_state("unsent"))
    if boundary == "replay":
        control.replay_request_state = _request_state("replay")
    socket = SimpleNamespace(close=AsyncMock())
    upstream = SimpleNamespace(close=AsyncMock())
    proxy = SimpleNamespace(_fail_pending_websocket_requests=AsyncMock())
    await _process_upstream_websocket_transport_end(
        cast(_WebSocketServiceProtocol, proxy),
        cast(WebSocket, socket),
        cast(UpstreamWebSocket, upstream),
        message=SimpleNamespace(kind="close", close_code=1000),
        account=cast(Account, SimpleNamespace(id="account")),
        account_id_value="account",
        pending_requests=pending,
        pending_lock=anyio.Lock(),
        client_send_lock=anyio.Lock(),
        api_key=None,
        upstream_control=control,
        response_create_gate=asyncio.Semaphore(1),
        downstream_activity=_DownstreamWebSocketActivity(),
        codex_session_affinity=boundary != "public",
    )
    socket.close.assert_not_awaited()
    if boundary == "pending":
        assert len(pending) == 1


async def test_request_admitted_while_close_waits_is_not_treated_as_idle() -> None:
    control = _WebSocketUpstreamControl(completed_response_delivered=True)
    socket = SimpleNamespace(close=AsyncMock())
    upstream = SimpleNamespace(close=AsyncMock())
    proxy = SimpleNamespace(_fail_pending_websocket_requests=AsyncMock())
    pending = deque()
    send_lock = anyio.Lock()
    await send_lock.acquire()
    task = asyncio.create_task(
        _process_upstream_websocket_transport_end(
            cast(_WebSocketServiceProtocol, proxy),
            cast(WebSocket, socket),
            cast(UpstreamWebSocket, upstream),
            message=SimpleNamespace(kind="close", close_code=1000),
            account=cast(Account, SimpleNamespace(id="account")),
            account_id_value="account",
            pending_requests=pending,
            pending_lock=anyio.Lock(),
            client_send_lock=send_lock,
            api_key=None,
            upstream_control=control,
            response_create_gate=asyncio.Semaphore(1),
            downstream_activity=_DownstreamWebSocketActivity(),
            codex_session_affinity=True,
        )
    )
    try:
        await asyncio.sleep(0)
        pending.append(_request_state("new-unsent"))
    finally:
        send_lock.release()
    await task
    socket.close.assert_not_awaited()
    assert len(pending) == 1
