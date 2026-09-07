from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from app.core.clients.proxy_websocket import UpstreamWebSocketMessage
from app.core.clock import clock_for
from app.core.config.settings import get_settings
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.proxy._service.websocket import steering
from tests.integration.test_astra_steering_parent_id import _configure_route, _run_route
from tests.integration.test_astra_steering_retirement import _ClosingUpstream, _history
from tests.unit.test_astra_steering_protocol import ScriptedSocket, create, response, saw
from tests.unit.test_proxy_utils import _make_account

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("anonymous_terminal", [False, True], ids=["identified", "anonymous"])
async def test_expired_steering_discards_submissions_and_rotates_with_late_event_protection(
    app_instance: FastAPI, monkeypatch: pytest.MonkeyPatch, anonymous_terminal: bool
) -> None:
    monkeypatch.setattr(steering, "_MAX_STEERING_HISTORY_IDS", 3)
    monkeypatch.setattr(get_settings(), "stream_idle_timeout_seconds", 300.0)
    parent_ids = ["p1", "p2"] if anonymous_terminal else ["p1", "p2", "p3"]
    accepted: set[str] = set()
    expired: dict[str, str] = {}
    expired_parents: set[str] = set()
    failures: list[str] = []
    retained_on_expiry: list[set[str]] = []
    late_notification_owners: list[set[str]] = []
    initial_histories = []
    late_processed = asyncio.Event()
    late_terminal = (
        {"type": "error", "error": {"code": "server_error", "message": "Late automatic successor"}}
        if anonymous_terminal
        else response("response.completed", "late-first", parent="p1")
    )

    class Socket(ScriptedSocket):
        async def send_text(self, text: str) -> None:
            event = json.loads(text)
            if event["type"] == "response.failed":
                assert event["response"]["error"]["code"] == "upstream_request_timeout"
                parent_id = expired[event["response"]["id"]]
                failures.append(parent_id)
                assert upstream.control is not None
                retained_on_expiry.append(set(upstream.control.steering_continuations))
                if parent_id == "p1":
                    late_steer = {"id": "expired-p1", "previous_response_id": "p1"}
                    for value in [
                        {"type": "response.steer.accepted", "steer": late_steer},
                        {
                            "type": "response.steer.pending",
                            "steer": late_steer,
                            "reason": "waiting_for_required_input",
                            "required_input": [{"type": "function_call_output", "call_id": "late-tool"}],
                        },
                        response("response.created", "late-first", parent="p1"),
                        late_terminal,
                    ]:
                        upstream.messages.put_nowait(UpstreamWebSocketMessage(kind="text", text=json.dumps(value)))
            await super().send_text(text)

    socket = Socket([])
    events = []
    for index, parent_id in enumerate(parent_ids):

        def prior_ready(sent, count=index):
            return saw("response.completed", "retry-success")(sent) if count == 1 else len(failures) >= count

        socket.scripts.extend(
            [
                (create(input_items=parent_id), prior_ready),
                (
                    {"type": "response.steer", "previous_response_id": parent_id, "input": "Correction " * 8192},
                    saw("response.completed", parent_id),
                ),
            ]
        )
        created_events = [response("response.created", parent_id), response("response.completed", parent_id)]
        if index == 1 and not anonymous_terminal:
            # The ordinary create is pending while these old identified events arrive.
            created_events[:0] = [
                response("response.created", "late-again", parent="p1"),
                response("response.completed", "late-again", parent="p1"),
            ]
        events.extend(
            [
                created_events,
                [
                    {
                        "type": "response.steer.accepted",
                        "steer": {"id": f"expired-{parent_id}", "previous_response_id": parent_id},
                    }
                ],
            ]
        )
        if index == 0:
            socket.scripts.append(
                (
                    {"type": "response.steer", "previous_response_id": "p1", "input": "Retry correction"},
                    lambda _: late_processed.is_set(),
                )
            )
            events.append(
                [
                    {"type": "response.steer.accepted", "steer": {"id": "retry-p1", "previous_response_id": "p1"}},
                    response("response.created", "retry-success", parent="p1"),
                    response("response.completed", "retry-success", parent="p1"),
                ]
            )
    socket.scripts.append((create(input_items="Fresh connection"), lambda _: len(failures) == len(parent_ids)))
    socket.finish_when = lambda event: saw("response.completed", "fresh")([event])
    upstream = _ClosingUpstream(events, socket)
    fresh = _ClosingUpstream([[response("response.created", "fresh"), response("response.completed", "fresh")]], socket)
    service, api_key, reservations = await _configure_route(app_instance, monkeypatch, upstream)
    account = _make_account("acc_steering_parent_id")
    connect = AsyncMock(side_effect=[(account, upstream), (account, fresh)])
    monkeypatch.setattr(service, "_connect_proxy_websocket", connect)
    original_relay = service._relay_upstream_websocket_messages
    original_process = service._process_upstream_websocket_text
    original_timeout = service._next_websocket_receive_timeout
    health_calls = []
    for owner, name in [
        (service, "_handle_stream_error"),
        (service._load_balancer, "record_errors"),
        (service._load_balancer, "record_error_backoff"),
    ]:
        call = AsyncMock()
        health_calls.append(call)
        monkeypatch.setattr(owner, name, call)

    async def relay(websocket, connection, **kwargs):
        connection.control = kwargs["upstream_control"]
        connection.pending = kwargs["pending_requests"]
        initial_histories.append(_history(connection.control))
        await original_relay(websocket, connection, **kwargs)

    async def process(text, **kwargs):
        event = json.loads(text)
        result = await original_process(text, **kwargs)
        if event["type"] == "response.steer.accepted":
            accepted.add(event["steer"]["previous_response_id"])
        if (
            event["type"] in {"response.steer.accepted", "response.steer.pending"}
            and event["steer"]["id"] == "expired-p1"
            and "p1" in failures
        ):
            late_notification_owners.append(set(kwargs["upstream_control"].steering_continuations))
        if event == late_terminal:
            late_processed.set()
            socket.changed.set()
        return result

    async def next_timeout(pending_requests, **kwargs):
        async with kwargs["pending_lock"]:
            for state in pending_requests:
                parent_id = state.steering_parent_response_id
                if parent_id is not None and parent_id in accepted and parent_id not in expired_parents:
                    state.started_at = clock_for(service).monotonic() - kwargs["proxy_request_budget_seconds"] - 1
                    expired[state.request_id] = parent_id
                    expired_parents.add(parent_id)
        return await original_timeout(pending_requests, **kwargs)

    monkeypatch.setattr(service, "_relay_upstream_websocket_messages", relay)
    monkeypatch.setattr(service, "_process_upstream_websocket_text", process)
    monkeypatch.setattr(service, "_next_websocket_receive_timeout", next_timeout)
    try:
        await _run_route(app_instance, socket)
    except TimeoutError:
        pytest.fail(
            f"Expiry retained steering parents {retained_on_expiry}; "
            f"failures={failures}, connections={connect.await_count}; "
            f"events={[(event['type'], event.get('error')) for event in socket.sent]}"
        )
    assert await service.drain_persistence_tasks(timeout_seconds=2)
    assert failures == parent_ids
    assert retained_on_expiry == [set() for _ in parent_ids]
    assert late_notification_owners == [set(), set()]
    assert initial_histories == [(frozenset(), frozenset(), 0)] * 2
    assert upstream.history_at_close == (
        frozenset(parent_ids),
        frozenset({"late-first"}) if anonymous_terminal else frozenset(),
        0,
    )
    assert upstream.pending_at_close == []
    assert len(fresh.sent) == 1 and fresh.sent[0]["input"][-1]["content"][0]["text"] == "Fresh connection"
    assert not [event for event in socket.sent if event["type"] in {"error", "response.steer.failed"}]
    assert [event["response"]["id"] for event in socket.sent if event["type"] == "response.created"] == [
        "p1",
        "retry-success",
        *parent_ids[1:],
        "fresh",
    ]
    for call in health_calls:
        call.assert_not_awaited()
    expected_statuses = ["finalized", "released", "finalized"]
    for _ in parent_ids[1:]:
        expected_statuses.extend(["finalized", "released"])
    expected_statuses.append("finalized")
    assert len(reservations) == len(expected_statuses)
    async with SessionLocal() as session:
        repository = ApiKeysRepository(session)
        rows = [await repository.get_usage_reservation(item.reservation_id) for item in reservations]
        assert [row.status for row in rows if row is not None] == expected_statuses
        limits = await repository.get_limits_by_key(api_key.id)
        assert limits[0].current_value == 14 * (len(parent_ids) + 2)
