from __future__ import annotations

import asyncio
from collections import deque
from unittest.mock import AsyncMock

import anyio
import pytest

from app.core.clock import clock_for
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.support import (
    _WebSocketRequestState,
    _WebSocketSteeringContinuation,
    _WebSocketUpstreamControl,
)
from app.modules.proxy._service.websocket import steering
from tests.unit.test_proxy_utils import _repo_factory, _RequestLogsRecorder

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [False, True], ids=["placeholder", "explicit"])
@pytest.mark.parametrize("replaced", [False, True], ids=["current-owner", "newer-owner"])
async def test_expiry_retires_only_its_current_steering_owner(
    monkeypatch: pytest.MonkeyPatch, explicit: bool, replaced: bool
) -> None:
    logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(logs))
    release_reservation = AsyncMock()
    monkeypatch.setattr(service, "_release_websocket_reservation", release_reservation)
    monkeypatch.setattr(steering, "_MAX_STEERING_HISTORY_IDS", 1)
    now = clock_for(service).monotonic()

    def state(request_id: str, started_at: float) -> _WebSocketRequestState:
        return _WebSocketRequestState(
            request_id=request_id,
            model="gpt-6-astra",
            service_tier=None,
            reasoning_effort=None,
            api_key_reservation=None,
            started_at=started_at,
        )

    parent = state("parent", now)
    parent.response_id = "p1"
    expired = state("expired", now - 10)
    expired.steering_parent_response_id = "p1"
    if explicit:
        expired.previous_response_id = "p1"
        expired.request_text = '{"type":"response.create","previous_response_id":"p1"}'
        expired.response_create_dispatched = True
    gate = asyncio.Semaphore(0)
    expired.response_create_gate = gate
    expired.response_create_gate_acquired = True
    newer = state("newer", now)
    if replaced:
        newer.steering_parent_response_id = "p1"
    continuation = _WebSocketSteeringContinuation(
        parent=parent, request_state=newer if replaced else expired, explicit_request_prepared=explicit
    )
    control = _WebSocketUpstreamControl(steering_continuations={"p1": continuation})
    pending = deque([expired, newer])
    lock = anyio.Lock()

    # Repeated timeout passes must not release the old owner twice or retire
    # a replacement that now owns the same parent ID.
    for _ in range(2):
        await service._fail_expired_pending_websocket_requests(
            account_id_value="acc_expiry",
            pending_requests=pending,
            pending_lock=lock,
            request_budget_seconds=5,
            error_code="upstream_request_timeout",
            error_message="Proxy request budget exhausted",
            api_key=None,
            upstream_control=control,
            response_create_gate=gate,
        )

    assert list(pending) == [newer]
    assert control.steering_continuations == ({"p1": continuation} if replaced else {})
    assert control.rejected_steering_parent_ids == (set() if replaced else {"p1"})
    assert control.retire_after_drain is not replaced
    assert not control.reconnect_requested
    assert not control.retired_steering_requests
    assert gate._value == 1
    release_reservation.assert_awaited_once_with(None)
    assert await service.drain_persistence_tasks(timeout_seconds=1)
    assert len(logs.calls) == 1
    assert logs.calls[0]["request_id"] == "expired"
    assert logs.calls[0]["error_code"] == "upstream_request_timeout"
