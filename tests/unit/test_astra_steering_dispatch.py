from __future__ import annotations

import asyncio
import json
from collections import deque

import anyio
import pytest

from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.support import (
    _WebSocketRequestState,
    _WebSocketSteeringContinuation,
    _WebSocketUpstreamControl,
)
from tests.unit.test_proxy_utils import _make_account, _repo_factory, _RequestLogsRecorder

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_late_successor_does_not_assign_or_release_unrelated_create() -> None:
    logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(logs))
    account = _make_account("acc_astra_late")
    gate = asyncio.Semaphore(0)

    def state(request_id: str) -> _WebSocketRequestState:
        return _WebSocketRequestState(
            request_id=request_id,
            model="gpt-6-astra",
            service_tier=None,
            reasoning_effort=None,
            api_key_reservation=None,
            started_at=0.0,
        )

    parent = state("parent")
    parent.response_id = "r1"
    expired = state("expired")
    expired.steering_parent_response_id = "r1"
    unrelated = state("unrelated")
    unrelated.response_create_gate_acquired = True
    unrelated.response_create_gate = gate
    pending = deque([unrelated])
    control = _WebSocketUpstreamControl(
        steering_continuations={"r1": _WebSocketSteeringContinuation(parent=parent, request_state=expired)}
    )
    lock = anyio.Lock()

    async def process(event: str, response_id: str, previous: str | None) -> None:
        await service._process_upstream_websocket_text(
            json.dumps(
                {
                    "type": event,
                    "response": {
                        "id": response_id,
                        "model": "gpt-6-astra",
                        "previous_response_id": previous,
                        "output": [],
                    },
                }
            ),
            account=account,
            account_id_value=account.id,
            pending_requests=pending,
            pending_lock=lock,
            api_key=None,
            upstream_control=control,
            response_create_gate=gate,
        )

    # The continuation was removed by expiry or while its explicit replacement
    # was being prepared; a late server-created response still names its parent.
    await process("response.created", "r-late", "r1")
    assert unrelated.response_id is None
    assert gate.locked()
    assert control.suppress_downstream_event is True
    control.suppress_downstream_event = False
    await process("response.completed", "r-late", "r1")
    assert list(pending) == [unrelated]
    assert not logs.calls
    assert control.suppress_downstream_event is True
    assert "r-late" not in control.suppressed_steering_response_ids
    control.suppress_downstream_event = False

    # The ordinary queued request still owns its real created event and gate.
    await process("response.created", "r-unrelated", None)
    assert unrelated.response_id == "r-unrelated"
    assert not gate.locked()
    assert control.suppress_downstream_event is False


@pytest.mark.asyncio
async def test_late_successor_anonymous_error_does_not_settle_unrelated_create() -> None:
    logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(logs))
    account = _make_account("acc_astra_late_anon")
    gate = asyncio.Semaphore(0)

    def state(request_id: str) -> _WebSocketRequestState:
        return _WebSocketRequestState(
            request_id=request_id,
            model="gpt-6-astra",
            service_tier=None,
            reasoning_effort=None,
            api_key_reservation=None,
            started_at=0.0,
        )

    parent = state("parent")
    parent.response_id = "r1"
    expired = state("expired")
    expired.steering_parent_response_id = "r1"
    unrelated = state("unrelated")
    unrelated.response_create_gate_acquired = True
    unrelated.response_create_gate = gate
    pending = deque([unrelated])
    control = _WebSocketUpstreamControl(
        steering_continuations={"r1": _WebSocketSteeringContinuation(parent=parent, request_state=expired)}
    )
    lock = anyio.Lock()

    await service._process_upstream_websocket_text(
        json.dumps(
            {
                "type": "response.created",
                "response": {
                    "id": "r-late",
                    "model": "gpt-6-astra",
                    "previous_response_id": "r1",
                    "output": [],
                },
            }
        ),
        account=account,
        account_id_value=account.id,
        pending_requests=pending,
        pending_lock=lock,
        api_key=None,
        upstream_control=control,
        response_create_gate=gate,
    )
    assert control.suppressed_steering_anonymous_terminals == 1
    control.suppress_downstream_event = False

    await service._process_upstream_websocket_text(
        json.dumps(
            {
                "type": "response.created",
                "response": {
                    "id": "r-unrelated",
                    "model": "gpt-6-astra",
                    "output": [],
                },
            }
        ),
        account=account,
        account_id_value=account.id,
        pending_requests=pending,
        pending_lock=lock,
        api_key=None,
        upstream_control=control,
        response_create_gate=gate,
    )
    assert unrelated.response_id == "r-unrelated"
    control.suppress_downstream_event = False

    await service._process_upstream_websocket_text(
        json.dumps({"error": {"code": "server_error", "message": "successor crashed"}}),
        account=account,
        account_id_value=account.id,
        pending_requests=pending,
        pending_lock=lock,
        api_key=None,
        upstream_control=control,
        response_create_gate=gate,
    )

    assert list(pending) == [unrelated]
    assert unrelated.response_id == "r-unrelated"
    assert not logs.calls
    assert control.suppress_downstream_event is True
    assert control.suppressed_steering_anonymous_terminals == 0


@pytest.mark.asyncio
async def test_unrelated_anonymous_error_is_not_consumed_as_successor_tombstone() -> None:
    logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(logs))
    account = _make_account("acc_astra_visible_anon")
    gate = asyncio.Semaphore(0)

    def state(request_id: str) -> _WebSocketRequestState:
        return _WebSocketRequestState(
            request_id=request_id,
            model="gpt-6-astra",
            service_tier=None,
            reasoning_effort=None,
            api_key_reservation=None,
            started_at=0.0,
        )

    parent = state("parent")
    parent.response_id = "r1"
    expired = state("expired")
    expired.steering_parent_response_id = "r1"
    unrelated = state("unrelated")
    unrelated.response_create_gate_acquired = True
    unrelated.response_create_gate = gate
    pending = deque([unrelated])
    control = _WebSocketUpstreamControl(
        steering_continuations={"r1": _WebSocketSteeringContinuation(parent=parent, request_state=expired)}
    )
    lock = anyio.Lock()

    await service._process_upstream_websocket_text(
        json.dumps(
            {
                "type": "response.created",
                "response": {
                    "id": "r-late",
                    "model": "gpt-6-astra",
                    "previous_response_id": "r1",
                    "output": [],
                },
            }
        ),
        account=account,
        account_id_value=account.id,
        pending_requests=pending,
        pending_lock=lock,
        api_key=None,
        upstream_control=control,
        response_create_gate=gate,
    )
    assert control.suppressed_steering_anonymous_terminals == 1
    control.suppress_downstream_event = False

    await service._process_upstream_websocket_text(
        json.dumps({"error": {"code": "server_error", "message": "visible request failed"}}),
        account=account,
        account_id_value=account.id,
        pending_requests=pending,
        pending_lock=lock,
        api_key=None,
        upstream_control=control,
        response_create_gate=gate,
    )

    assert unrelated not in pending
    assert control.suppressed_steering_anonymous_terminals == 1
    assert control.suppress_downstream_event is False
