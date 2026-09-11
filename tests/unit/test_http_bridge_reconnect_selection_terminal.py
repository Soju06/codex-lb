"""Reader reconnect preserves ordinary selection errors without inventing owner loss."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.core.clients.proxy_websocket import UpstreamWebSocketMessage
from app.modules.proxy import service as proxy_service
from tests.unit.test_proxy_http_bridge import (
    _make_app_settings,
    _make_bridge_session,
    _make_eventless_http_bridge_owner,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize("required_owner", [False, True], ids=["ordinary", "file-owner"])
async def test_reader_reconnect_selection_terminal_keeps_owner_classification(
    monkeypatch: pytest.MonkeyPatch,
    required_owner: bool,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: _make_app_settings())
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(prefer_earlier_reset_accounts=False, routing_strategy=None))
        ),
    )
    monkeypatch.setattr(service, "_handle_stream_error", AsyncMock())
    monkeypatch.setattr(service._durable_bridge, "lookup_retry_circuit", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_persist_http_bridge_retry_circuit", AsyncMock())
    session = _make_bridge_session(
        key=proxy_service._HTTPBridgeSessionKey("prompt_cache", "selection-terminal", None),
    )
    request = _make_eventless_http_bridge_owner(sent_at=time.monotonic())
    request.started_at = time.monotonic()
    request.skip_request_log = True
    request.request_text = json.dumps({"type": "response.create", "model": request.model, "input": []})
    request.file_required_preferred_account = required_owner
    request.preferred_account_id = session.account.id if required_owner else None
    session.pending_requests.append(request)
    session.queued_request_count = 1
    session.account_lease = await service._load_balancer.acquire_account_lease(session.account.id, kind="stream")
    assert session.account_lease is not None
    service._http_bridge_sessions[session.key] = session
    upstream = cast(Any, session.upstream)
    upstream.receive = AsyncMock(return_value=UpstreamWebSocketMessage(kind="close", close_code=1000))
    select = AsyncMock(
        return_value=proxy_service.AccountSelection(
            account=None, error_message="No available accounts", error_code="no_accounts"
        )
    )
    monkeypatch.setattr(service, "_select_account_with_budget_for_stream", select)
    connect = AsyncMock()
    monkeypatch.setattr(service, "_open_upstream_websocket_with_budget", connect)
    reader = asyncio.create_task(service._relay_http_bridge_upstream_messages(session))
    session.upstream_reader = reader
    assert request.event_queue is not None
    try:
        terminal = await asyncio.wait_for(request.event_queue.get(), timeout=5.0)
        sentinel = await asyncio.wait_for(request.event_queue.get(), timeout=5.0)
        # Ordinary waiterless retirement can cancel its reader on upstream main.
        # The regression contract is the emitted terminal and drained resources.
        results = await asyncio.wait_for(asyncio.gather(reader, return_exceptions=True), timeout=5.0)
    finally:
        if not reader.done():
            reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)

    assert select.await_count in {1, 2}
    assert all(
        call.kwargs["preferred_account_is_continuity_owner"] is required_owner for call in select.await_args_list
    )
    connect.assert_not_awaited()
    assert terminal is not None
    expected = "previous_response_owner_unavailable" if required_owner else "no_accounts"
    assert request.error_code_override == expected
    assert f'"code":"{expected}"' in terminal.replace(" ", "")
    assert sentinel is None
    if required_owner:
        assert not reader.cancelled()
    assert results[0] is None or (not required_owner and isinstance(results[0], asyncio.CancelledError))
    assert request.event_queue.empty()
    assert not session.pending_requests
    assert not session.handoff_in_progress
    assert session.key not in service._http_bridge_inflight_sessions
    assert session.key not in service._http_bridge_sessions
    assert id(session) not in service._http_bridge_detached_sessions
    assert session.account_lease is None
    upstream.close.assert_awaited_once()
