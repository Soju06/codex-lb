"""Reader-origin owner loss finishes without cancelling its own caller."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import nullcontext
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.core.clients.proxy_websocket import UpstreamWebSocketMessage
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.http_bridge import helpers as http_bridge_helpers
from app.modules.proxy._service.support import _HTTPBridgeResponseCreateAttempt
from tests.unit.test_http_bridge_accepted_retirement import _configure
from tests.unit.test_proxy_http_bridge import (
    _activate_half_open_probe,
    _make_bridge_session,
    _make_eventless_http_bridge_owner,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize("reader_is_caller", [True, False], ids=["reader-caller", "foreign-reader"])
@pytest.mark.parametrize("close_code", [1000, 1011, None], ids=["clean-close", "error-close", "created-timeout"])
async def test_reader_owner_loss_finishes_typed_cleanup_without_cancelling_reader(
    monkeypatch: pytest.MonkeyPatch,
    reader_is_caller: bool,
    close_code: int | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    _configure(monkeypatch, service)
    session = _make_bridge_session(key_value="reader-owner-loss")
    request = _make_eventless_http_bridge_owner(sent_at=time.monotonic())
    request.started_at = time.monotonic()
    request.skip_request_log = True
    request.request_text = json.dumps({"type": "response.create", "model": request.model, "input": []})
    request.file_required_preferred_account = True
    request.preferred_account_id = session.account.id
    attempt = _HTTPBridgeResponseCreateAttempt(ordinal=1)
    request.response_create_attempt = attempt
    session.pending_requests.append(request)
    session.queued_request_count = 1
    service._http_bridge_sessions[session.key] = session
    circuit = _activate_half_open_probe(service, session)
    circuit.half_open_until = 0.0
    circuit.half_open_owner_session = None
    circuit.cooldown_until = time.monotonic() - 1.0
    upstream = cast(Any, session.upstream)
    upstream.receive = AsyncMock(return_value=UpstreamWebSocketMessage(kind="close", close_code=close_code))
    if close_code is None:
        request.skip_request_log = False
        monkeypatch.setattr(service, "_write_request_log", AsyncMock())
        request.started_at -= 2.0
        request.response_create_sent_at = request.started_at
        monkeypatch.setattr(http_bridge_helpers, "HTTP_BRIDGE_STUCK_GATE_RETIRE_AFTER_SECONDS", 1.0)
        upstream.receive = AsyncMock(side_effect=asyncio.Event().wait)
    select = AsyncMock(
        return_value=proxy_service.AccountSelection(
            account=None, error_message="Preferred account is unavailable", error_code="usage_limit_reached"
        )
    )
    monkeypatch.setattr(service, "_select_account_with_budget_for_stream", select)

    async def wait_for_foreign_reader_cancellation() -> None:
        await asyncio.Event().wait()

    reader = asyncio.create_task(service._relay_http_bridge_upstream_messages(session))
    foreign_reader = None if reader_is_caller else asyncio.create_task(wait_for_foreign_reader_cancellation())
    session.upstream_reader = reader if reader_is_caller else foreign_reader

    try:
        await asyncio.wait_for(asyncio.shield(reader), timeout=5.0)
        if foreign_reader is not None:
            assert foreign_reader.cancelled()
    finally:
        tasks = [reader] if foreign_reader is None else [reader, foreign_reader]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    select.assert_awaited_once()
    assert reader.cancelled() is False
    assert session.upstream_reader is None
    assert request.event_queue is not None
    terminal = request.event_queue.get_nowait()
    assert terminal is not None
    assert "previous_response_owner_unavailable" in terminal
    assert request.event_queue.get_nowait() is None
    assert request.event_queue.empty()
    upstream.close.assert_awaited_once()
    assert not session.pending_requests
    assert session.key not in service._http_bridge_sessions
    assert id(session) not in service._http_bridge_detached_sessions
    assert circuit.half_open_until == 0.0
    assert circuit.consecutive_failures == 2
    assert attempt.disarmed
    assert "Timed out waiting" not in caplog.text
    cast(AsyncMock, service._handle_stream_error).assert_not_awaited()
