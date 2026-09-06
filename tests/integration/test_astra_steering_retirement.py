from __future__ import annotations

import asyncio
import json
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from app.core.clients.proxy import ProxyResponseError
from app.core.clients.proxy_websocket import UpstreamWebSocketMessage
from app.core.errors import openai_error
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.support import _WebSocketRequestState, _WebSocketUpstreamControl
from app.modules.proxy._service.websocket import steering
from app.modules.proxy.load_balancer import AccountLease
from app.modules.proxy.work_admission import WorkAdmissionController
from tests.integration.test_astra_steering_dispatch import _use_websocket_route
from tests.unit.test_astra_steering_protocol import ScriptedSocket, ScriptedUpstream, create, response, run_socket, saw

pytestmark = pytest.mark.integration

History = tuple[frozenset[str], frozenset[str], int]


def _history(control: _WebSocketUpstreamControl) -> History:
    return (
        frozenset(control.rejected_steering_parent_ids),
        frozenset(control.suppressed_steering_response_ids),
        control.suppressed_steering_anonymous_terminals,
    )


def _steer(parent: str) -> dict:
    return {"type": "response.steer", "previous_response_id": parent, "input": "Correction"}


def _rejected(parent: str) -> list[dict]:
    steer = {"id": f"steer-{parent}", "previous_response_id": parent}
    return [
        {"type": "response.steer.accepted", "steer": steer},
        {
            "type": "response.steer.failed",
            "steer": steer,
            "error": {"code": "successor_creation_failed", "message": "Rejected"},
        },
    ]


def _anonymous_terminal() -> dict:
    return {"type": "error", "error": {"code": "server_error", "message": "Suppressed automatic successor"}}


class _ObservedUpstream(ScriptedUpstream):
    def __init__(self, events: list[list[dict]], socket: ScriptedSocket) -> None:
        super().__init__(events)
        self.socket = socket
        self.closed = asyncio.Event()
        self.control: _WebSocketUpstreamControl | None = None
        self.pending: deque[_WebSocketRequestState] = deque()
        self.history_at_close: History | None = None
        self.pending_at_close: list[_WebSocketRequestState] | None = None
        self.timeline: list[tuple[str, str]] = []
        self.name = ""

    async def close(self) -> None:
        if not self.closed.is_set():
            assert self.control is not None
            self.history_at_close = _history(self.control)
            self.pending_at_close = list(self.pending)
            self.timeline.append(("close", self.name))
            self.closed.set()
            self.socket.changed.set()


@dataclass
class _RetirementAudit:
    controls: list[_WebSocketUpstreamControl] = field(default_factory=list)
    initial_history: list[History] = field(default_factory=list)
    histories: list[list[History]] = field(default_factory=list)
    anonymous_processed: set[int] = field(default_factory=set)
    states: list[_WebSocketRequestState] = field(default_factory=list)
    heartbeats: list[asyncio.Task] = field(default_factory=list)
    leases: list[AccountLease] = field(default_factory=list)
    released_leases: list[AccountLease] = field(default_factory=list)
    failed_requests: list[_WebSocketRequestState] = field(default_factory=list)
    timeline: list[tuple[str, str]] = field(default_factory=list)
    reserve: AsyncMock = field(default_factory=AsyncMock)
    health_error: AsyncMock = field(default_factory=AsyncMock)
    account_penalties: list[AsyncMock] = field(default_factory=list)
    controller: WorkAdmissionController = field(
        default_factory=lambda: WorkAdmissionController(
            token_refresh_limit=2,
            websocket_connect_limit=2,
            response_create_limit=4,
            compact_response_create_limit=2,
        )
    )

    def configure(self, service, account, *, monkeypatch, app_instance, socket, upstreams) -> None:
        monkeypatch.setattr(
            service, "_connect_proxy_websocket", AsyncMock(side_effect=[(account, item) for item in upstreams])
        )
        original_relay = service._relay_upstream_websocket_messages
        original_process = service._process_upstream_websocket_text
        original_prepare = service._prepare_response_bridge_request_state
        original_settle = service._settle_stream_api_key_usage
        original_fail = service._fail_pending_websocket_requests
        self.reserve = AsyncMock(wraps=service._reserve_websocket_api_key_usage)

        async def relay(websocket, upstream, **kwargs):
            control = kwargs["upstream_control"]
            upstream.control = control
            upstream.pending = kwargs["pending_requests"]
            upstream.timeline = self.timeline
            upstream.name = f"generation-{len(self.controls)}"
            self.controls.append(control)
            self.initial_history.append(_history(control))
            self.histories.append([])
            await original_relay(websocket, upstream, **kwargs)

        async def process(text, **kwargs):
            result = await original_process(text, **kwargs)
            control = kwargs["upstream_control"]
            generation = next(index for index, item in enumerate(self.controls) if item is control)
            self.histories[generation].append(_history(control))
            if json.loads(text) == _anonymous_terminal():
                self.anonymous_processed.add(generation)
                socket.changed.set()
            return result

        def prepare(*args, **kwargs):
            state, text = original_prepare(*args, **kwargs)
            self.states.append(state)
            return state, text

        async def settle(key, reservation, settlement, request_id, **kwargs):
            result = await original_settle(key, reservation, settlement, request_id, **kwargs)
            self.timeline.append(("settled", request_id))
            return result

        async def fail(**kwargs):
            self.failed_requests.extend(kwargs["pending_requests"])
            return await original_fail(**kwargs)

        async def heartbeat(**kwargs):
            task = asyncio.current_task()
            assert task is not None
            self.heartbeats.append(task)
            await kwargs["stop_event"].wait()

        async def acquire(**kwargs):
            lease = AccountLease(f"lease-{len(self.leases)}", account.id, "response_create", 0.0)
            self.leases.append(lease)
            return lease

        async def release(lease):
            if lease is not None:
                self.released_leases.append(lease)

        monkeypatch.setattr(service, "_relay_upstream_websocket_messages", relay)
        monkeypatch.setattr(service, "_process_upstream_websocket_text", process)
        monkeypatch.setattr(service, "_prepare_response_bridge_request_state", prepare)
        monkeypatch.setattr(service, "_settle_stream_api_key_usage", settle)
        monkeypatch.setattr(service, "_fail_pending_websocket_requests", fail)
        monkeypatch.setattr(service, "_reserve_websocket_api_key_usage", self.reserve)
        monkeypatch.setattr(service, "_handle_stream_error", self.health_error)
        for name in (
            "record_errors",
            "record_error_backoff",
            "mark_permanent_failure",
            "mark_rate_limit",
            "mark_quota_exceeded",
        ):
            penalty = AsyncMock()
            self.account_penalties.append(penalty)
            monkeypatch.setattr(service._load_balancer, name, penalty)
        monkeypatch.setattr(service, "_get_work_admission", lambda: self.controller)
        monkeypatch.delattr(service, "_start_request_state_api_key_reservation_heartbeat")
        monkeypatch.setattr(service, "_run_api_key_reservation_heartbeat", heartbeat)
        monkeypatch.setattr(service, "_acquire_account_response_create_lease_or_overload", acquire)
        monkeypatch.setattr(service._load_balancer, "release_account_lease", release)
        _use_websocket_route(app_instance, monkeypatch, service, socket)

    async def assert_clean(self, service, reservations, settled, released, *, expired_states=()) -> None:
        assert await service.drain_persistence_tasks(timeout_seconds=2)
        assert self.failed_requests == list(expired_states)
        self.health_error.assert_not_awaited()
        for penalty in self.account_penalties:
            penalty.assert_not_awaited()
        assert not service._background_cleanup_tasks
        for control in self.controls:
            assert not control.retired_steering_requests
            assert all(
                continuation.request_state in expired_states for continuation in control.steering_continuations.values()
            )
        assert self.heartbeats and all(task.done() for task in self.heartbeats)
        assert all(
            state.api_key_reservation is None
            and state.api_key_reservation_heartbeat_task is None
            and state.response_create_admission is None
            and not state.response_create_gate_acquired
            and state.account_response_create_lease is None
            for state in self.states
        )
        assert Counter(lease.lease_id for lease in self.leases) == Counter(
            lease.lease_id for lease in self.released_leases
        )
        assert self.controller._response_create is not None
        assert self.controller._response_create.semaphore._value == 4
        terminal_reservations = [item[0] for item in settled] + [
            call.args[0].reservation_id for call in released.await_args_list if call.args[0] is not None
        ]
        assert Counter(terminal_reservations) == Counter(item.reservation_id for item in reservations)


@pytest.mark.asyncio
async def test_rejected_and_anonymous_successor_history_rotates_multiple_upstream_generations(
    app_instance: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(steering, "_MAX_STEERING_HISTORY_IDS", 3)
    socket = ScriptedSocket([])
    socket.finish_when = lambda event: saw("response.completed", "r-fresh")([event])
    audit = _RetirementAudit()
    upstreams = []
    completed_ids = []
    for generation in range(3):
        first, second = f"r-{generation}-first", f"r-{generation}-second"
        suppressed = f"r-{generation}-suppressed"
        completed_ids.extend([first, second])
        events = [
            [response("response.created", first), response("response.completed", first)],
            [
                *_rejected(first),
                response("response.created", suppressed, parent=first),
                response("response.created", suppressed, parent=first),
                _anonymous_terminal(),
            ],
            [response("response.created", second), response("response.completed", second)],
            _rejected(second),
        ]
        upstreams.append(_ObservedUpstream(events, socket))
        socket.scripts.extend(
            [
                (create(), lambda _, index=generation: index == 0 or upstreams[index - 1].closed.is_set()),
                (_steer(first), saw("response.completed", first)),
                (create(), lambda _, index=generation: index in audit.anonymous_processed),
                (_steer(second), saw("response.completed", second)),
            ]
        )
    upstreams.append(
        _ObservedUpstream(
            [[response("response.created", "r-fresh"), response("response.completed", "r-fresh")]], socket
        )
    )
    socket.scripts.append((create(), lambda _: upstreams[2].closed.is_set()))

    def configure(service, account):
        audit.configure(
            service, account, monkeypatch=monkeypatch, app_instance=app_instance, socket=socket, upstreams=upstreams
        )

    service, reservations, settled, released, logs = await run_socket(
        monkeypatch, socket, upstreams[0], configure=configure
    )

    assert len(audit.controls) == 4
    assert len({id(control) for control in audit.controls}) == 4
    assert audit.initial_history == [(frozenset(), frozenset(), 0)] * 4
    assert audit.anonymous_processed == {0, 1, 2}
    for generation, upstream in enumerate(upstreams[:3]):
        assert upstream.history_at_close == (
            frozenset({f"r-{generation}-first", f"r-{generation}-second"}),
            frozenset({f"r-{generation}-suppressed"}),
            0,
        )
        assert upstream.pending_at_close == []
        assert upstream.control is not None and upstream.control.reconnect_requested
        history = audit.histories[generation]
        assert all(len(rejected) + len(suppressed) <= 3 for rejected, suppressed, _ in history)
        assert all(left[0] <= right[0] and left[1] <= right[1] for left, right in zip(history, history[1:]))
        assert [frame["type"] for frame in upstream.sent] == [
            "response.create",
            "response.steer",
            "response.create",
            "response.steer",
        ]
    assert len(reservations) == 13
    assert [item[3] for item in settled] == completed_ids + ["r-fresh"]
    assert all(item[1] == "success" for item in settled)
    assert [event["response"]["id"] for event in socket.sent if event["type"] == "response.created"] == (
        completed_ids + ["r-fresh"]
    )
    assert not [event for event in socket.sent if event["type"] in {"error", "response.failed"}]
    assert len(logs.calls) == 7
    await audit.assert_clean(service, reservations, settled, released)


class _ClosingUpstream(_ObservedUpstream):
    def __init__(self, events: list[list[dict]], socket: ScriptedSocket) -> None:
        super().__init__(events, socket)
        self.receive_waiting = asyncio.Event()

    async def receive(self):
        self.receive_waiting.set()
        try:
            return await super().receive()
        finally:
            self.receive_waiting.clear()

    async def close(self) -> None:
        was_closed = self.closed.is_set()
        await super().close()
        if not was_closed:
            # A local close wakes an outstanding receive in a real adapter.
            self.messages.put_nowait(UpstreamWebSocketMessage(kind="close", close_code=1000))


def _tool_wait_events() -> list[list[dict]]:
    call = {"type": "function_call", "call_id": "tool", "name": "slow", "arguments": "{}"}
    return [
        [response("response.created", "r-rejected"), response("response.completed", "r-rejected")],
        _rejected("r-rejected"),
        [response("response.created", "r-tool")],
        [
            {"type": "response.steer.accepted", "steer": {"id": "steer-tool", "previous_response_id": "r-tool"}},
            response("response.completed", "r-tool", output=[call]),
            {
                "type": "response.steer.pending",
                "steer": {"id": "steer-tool", "previous_response_id": "r-tool"},
                "reason": "waiting_for_required_input",
                "required_input": [{"type": "function_call_output", "call_id": "tool"}],
            },
        ],
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_path", ["request-timeout", "local-presend-error"])
async def test_history_retirement_rotates_after_last_pending_local_cleanup(
    app_instance: FastAPI, monkeypatch: pytest.MonkeyPatch, cleanup_path: str
) -> None:
    monkeypatch.setattr(steering, "_MAX_STEERING_HISTORY_IDS", 2)
    audit = _RetirementAudit()
    expected_code = "upstream_request_timeout" if cleanup_path == "request-timeout" else "account_response_create_cap"
    expired_states = []
    local_failure_seen = asyncio.Event()
    next_create_received = asyncio.Event()
    cap_processed = asyncio.Event()

    class Socket(ScriptedSocket):
        async def receive(self) -> dict:
            message = await super().receive()
            if message["type"] == "websocket.receive" and json.loads(message["text"]).get("input") == "After cleanup":
                assert local_failure_seen.is_set()
                assert not upstream.pending
                next_create_received.set()
            return message

        async def send_text(self, text: str) -> None:
            await super().send_text(text)
            event = json.loads(text)
            if event.get("type") == "response.failed" and event["response"]["error"]["code"] == expected_code:
                local_failure_seen.set()

    socket = Socket(
        [
            (create(), lambda _: True),
            (_steer("r-rejected"), saw("response.completed", "r-rejected")),
            (create(), saw("response.steer.failed")),
            (_steer("r-tool"), saw("response.created", "r-tool")),
        ]
    )
    if cleanup_path == "local-presend-error":
        socket.scripts.append(
            (
                create(
                    parent="r-tool",
                    input_items=[{"type": "function_call_output", "call_id": "tool", "output": "saved"}],
                ),
                saw("response.steer.pending"),
            )
        )
    socket.scripts.append((create(input_items="After cleanup"), lambda _: local_failure_seen.is_set()))
    socket.finish_when = lambda event: saw("response.completed", "r-after")([event])
    upstream = _ClosingUpstream(_tool_wait_events(), socket)
    suppressed_created = response("response.created", "r-suppressed", parent="r-rejected")
    if cleanup_path == "request-timeout":
        upstream.events[-1].append(suppressed_created)
    fresh = _ClosingUpstream(
        [[response("response.created", "r-after"), response("response.completed", "r-after")]], socket
    )

    def configure(service, account):
        audit.configure(
            service,
            account,
            monkeypatch=monkeypatch,
            app_instance=app_instance,
            socket=socket,
            upstreams=[upstream, fresh],
        )
        proxy_service.get_settings().sse_keepalive_interval_seconds = 0
        original_timeout = service._next_websocket_receive_timeout
        original_acquire = service._acquire_account_response_create_lease_or_overload
        original_process = service._process_upstream_websocket_text

        async def process(text, **kwargs):
            result = await original_process(text, **kwargs)
            if json.loads(text) == suppressed_created:
                assert kwargs["upstream_control"].retire_after_drain
                assert not kwargs["upstream_control"].reconnect_requested
                cap_processed.set()
            return result

        async def next_timeout(pending_requests, **kwargs):
            if cleanup_path == "request-timeout" and cap_processed.is_set() and not expired_states:
                async with kwargs["pending_lock"]:
                    assert len(pending_requests) == 1
                    state = pending_requests[0]
                    assert state.steering_parent_response_id == "r-tool" and state.request_text is None
                    # Advance this request's budget deterministically; both the
                    # timeout calculation and expired-request cleanup stay real.
                    state.started_at = time.monotonic() - kwargs["proxy_request_budget_seconds"] - 1
                    expired_states.append(state)
            return await original_timeout(pending_requests, **kwargs)

        async def acquire(**kwargs):
            state = next(item for item in audit.states if item.request_id == kwargs["request_id"])
            if (
                cleanup_path == "local-presend-error"
                and state.previous_response_id == "r-tool"
                and state.request_text is not None
            ):
                assert state in upstream.pending
                assert state.response_create_sent_at is None
                upstream.messages.put_nowait(UpstreamWebSocketMessage(kind="text", text=json.dumps(suppressed_created)))
                await asyncio.wait_for(cap_processed.wait(), timeout=1)
                await asyncio.wait_for(upstream.receive_waiting.wait(), timeout=1)
                assert not upstream.closed.is_set()
                raise ProxyResponseError(
                    429,
                    openai_error(
                        "account_response_create_cap",
                        "Account response-create capacity is exhausted",
                        error_type="rate_limit_error",
                    ),
                )
            return await original_acquire(**kwargs)

        monkeypatch.setattr(service, "_process_upstream_websocket_text", process)
        monkeypatch.setattr(service, "_next_websocket_receive_timeout", next_timeout)
        monkeypatch.setattr(service, "_acquire_account_response_create_lease_or_overload", acquire)

    operation = asyncio.create_task(run_socket(monkeypatch, socket, upstream, configure=configure))
    try:
        done, _ = await asyncio.wait({operation}, timeout=4)
        if not done:
            # Capture the stall before cancellation closes the old socket.
            assert cap_processed.is_set() and local_failure_seen.is_set() and next_create_received.is_set()
            assert not upstream.pending and not upstream.closed.is_set()
            assert upstream.receive_waiting.is_set()
            assert upstream.control is not None and not upstream.control.reconnect_requested
            pytest.fail(
                f"{cleanup_path}: original failure delivered, pending empty and next create received; "
                "old upstream remains open with its reader blocked in receive"
            )
        service, reservations, settled, released, logs = await operation
    finally:
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)

    assert len(audit.controls) == 2
    assert upstream.pending_at_close == []
    assert upstream.history_at_close == (frozenset({"r-rejected"}), frozenset({"r-suppressed"}), 1)
    assert len(upstream.sent) == 4
    assert len(fresh.sent) == 1
    assert fresh.sent[0]["input"] == [{"role": "user", "content": [{"type": "input_text", "text": "After cleanup"}]}]
    assert local_failure_seen.is_set() and next_create_received.is_set()
    failures = [event for event in socket.sent if event["type"] == "response.failed"]
    assert [event["response"]["error"]["code"] for event in failures] == [expected_code]
    assert not [event for event in socket.sent if event["type"] == "error"]
    assert audit.initial_history == [(frozenset(), frozenset(), 0)] * 2
    assert len(reservations) == (5 if cleanup_path == "request-timeout" else 6)
    assert [item[3] for item in settled] == ["r-rejected", "r-tool", "r-after"]
    assert all(item[1] == "success" for item in settled)
    assert [call.args[0].reservation_id for call in released.await_args_list if call.args[0] is not None] == (
        ["res_1", "res_3"] if cleanup_path == "request-timeout" else ["res_1", "res_3", "res_4"]
    )
    assert len(logs.calls) == (4 if cleanup_path == "request-timeout" else 3)
    await audit.assert_clean(service, reservations, settled, released, expired_states=expired_states)


@pytest.mark.asyncio
async def test_history_retirement_rejects_new_work_but_finishes_required_tool_input_before_rotation(
    app_instance: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(steering, "_MAX_STEERING_HISTORY_IDS", 2)
    audit = _RetirementAudit()
    call = {"type": "function_call", "call_id": "tool", "name": "slow", "arguments": "{}"}
    result = {"type": "function_call_output", "call_id": "tool", "output": "saved"}
    rejected_work_reservation_counts = []

    class Socket(ScriptedSocket):
        async def send_text(self, text: str) -> None:
            await super().send_text(text)
            event = json.loads(text)
            if event.get("error", {}).get("code") == "service_unavailable":
                rejected_work_reservation_counts.append(audit.reserve.await_count)
                assert not upstream.closed.is_set()

    socket = Socket(
        [
            (create(), lambda _: True),
            (_steer("r-rejected"), saw("response.completed", "r-rejected")),
            (create(), saw("response.steer.failed")),
            (_steer("r-tool"), saw("response.created", "r-tool")),
            (_steer("r-tool"), lambda _: 0 in audit.anonymous_processed),
            (
                create(input_items="Rejected unrelated request"),
                lambda events: any(
                    event["type"] == "response.steer.failed"
                    and event.get("error", {}).get("code") == "service_unavailable"
                    for event in events
                ),
            ),
            (
                create(parent="r-tool", input_items=[result]),
                lambda events: any(event["type"] == "error" and event.get("status") == 503 for event in events),
            ),
            (create(input_items="Fresh generation"), lambda _: upstream.closed.is_set()),
        ]
    )
    socket.finish_when = lambda event: saw("response.completed", "r-after")([event])
    upstream = _ObservedUpstream(
        [
            [response("response.created", "r-rejected"), response("response.completed", "r-rejected")],
            _rejected("r-rejected"),
            [response("response.created", "r-tool")],
            [
                {"type": "response.steer.accepted", "steer": {"id": "steer-tool", "previous_response_id": "r-tool"}},
                response("response.completed", "r-tool", output=[call]),
                {
                    "type": "response.steer.pending",
                    "steer": {"id": "steer-tool", "previous_response_id": "r-tool"},
                    "reason": "waiting_for_required_input",
                    "required_input": [{"type": "function_call_output", "call_id": "tool"}],
                },
                response("response.created", "r-suppressed", parent="r-rejected"),
                _anonymous_terminal(),
            ],
            [
                response("response.created", "r-explicit", parent="r-tool"),
                response("response.completed", "r-explicit", parent="r-tool"),
            ],
        ],
        socket,
    )
    fresh = _ObservedUpstream(
        [[response("response.created", "r-after"), response("response.completed", "r-after")]], socket
    )

    def configure(service, account):
        audit.configure(
            service,
            account,
            monkeypatch=monkeypatch,
            app_instance=app_instance,
            socket=socket,
            upstreams=[upstream, fresh],
        )

    service, reservations, settled, released, logs = await run_socket(
        monkeypatch, socket, upstream, configure=configure
    )

    assert rejected_work_reservation_counts == [4, 4]
    assert len(reservations) == 6
    service._extend_websocket_api_key_usage.assert_not_awaited()
    assert len(upstream.sent) == 5
    assert upstream.sent[-1]["previous_response_id"] == "r-tool"
    assert upstream.sent[-1]["input"] == [result]
    assert fresh.sent[0]["input"] != upstream.sent[-1]["input"]
    assert [(item[0], item[3]) for item in settled] == [
        ("res_0", "r-rejected"),
        ("res_2", "r-tool"),
        ("res_4", "r-explicit"),
        ("res_5", "r-after"),
    ]
    assert all(item[1] == "success" for item in settled)
    assert upstream.pending_at_close == []
    assert upstream.history_at_close == (frozenset({"r-rejected"}), frozenset({"r-suppressed"}), 0)
    assert audit.timeline.index(("settled", "r-explicit")) < audit.timeline.index(("close", "generation-0"))
    assert audit.timeline.index(("close", "generation-0")) < audit.timeline.index(("settled", "r-after"))
    assert audit.initial_history == [(frozenset(), frozenset(), 0)] * 2
    assert all(len(rejected) + len(suppressed) <= 2 for rejected, suppressed, _ in audit.histories[0])
    assert not [event for event in socket.sent if event["type"] == "response.failed"]
    assert len(logs.calls) == 4
    await audit.assert_clean(service, reservations, settled, released)
