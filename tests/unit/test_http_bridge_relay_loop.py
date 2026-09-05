"""Scheduling regressions for the HTTP bridge upstream relay loop.

The reader used to spawn a fresh ``upstream_reader_wakeup.wait()`` task for
every upstream message and cancel it through the long-lived-child cleanup
helper (``sleep(0)`` + ``cancel()`` + timed ``asyncio.wait``) as soon as the
message arrived — several event-loop trips of pure overhead per relayed
delta. These tests pin the persistent-waiter protocol: one wakeup task per
wait cycle, a send that lands mid-processing is consumed without a spurious
or lost wake, a send that lands while the loop is waiting still re-evaluates
the deadline exactly once, and loop exit cancels the long-lived waiter.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, Literal, cast
from unittest.mock import AsyncMock

import anyio
import pytest

from app.core.clients.proxy_websocket import UpstreamWebSocket, UpstreamWebSocketMessage
from app.db.models import AccountStatus
from app.modules.proxy import service as proxy_service

pytestmark = pytest.mark.unit


class _CountingWakeupEvent(asyncio.Event):
    def __init__(self) -> None:
        super().__init__()
        self.wait_calls = 0
        self.wait_cancellations = 0

    async def wait(self) -> Literal[True]:
        self.wait_calls += 1
        try:
            return await super().wait()
        except asyncio.CancelledError:
            self.wait_cancellations += 1
            raise


class _ScriptedUpstream:
    """Delivers the scripted text frames, then blocks until cancelled."""

    def __init__(self, texts: list[str]) -> None:
        self._texts = deque(texts)
        self.receive_calls = 0
        self.receive_cancellations = 0
        self.blocked = asyncio.Event()
        self.closed = False

    async def receive(self) -> UpstreamWebSocketMessage:
        self.receive_calls += 1
        if self._texts:
            return UpstreamWebSocketMessage(kind="text", text=self._texts.popleft())
        self.blocked.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.receive_cancellations += 1
            raise
        raise AssertionError("unreachable")

    async def send_text(self, text: str) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


def _make_bridge_session(upstream: _ScriptedUpstream) -> proxy_service._HTTPBridgeSession:
    key_value = "bridge-relay-loop"
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", key_value, None),
        headers={"x-codex-session-id": key_value},
        affinity=proxy_service._AffinityPolicy(
            key=key_value,
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.2",
        account=cast(
            Any,
            SimpleNamespace(id="acc-bridge", status=AccountStatus.ACTIVE, plan_type="plus", chatgpt_account_id=None),
        ),
        upstream=cast(UpstreamWebSocket, upstream),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )
    session.upstream_reader_wakeup = _CountingWakeupEvent()
    return session


def _relay_settings() -> SimpleNamespace:
    return SimpleNamespace(
        sse_keepalive_interval_seconds=0.0,
        stream_idle_timeout_seconds=60.0,
        http_responses_session_bridge_request_budget_seconds=60.0,
        http_responses_session_bridge_stuck_gate_retire_after_seconds=300.0,
    )


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


def _start_relay(
    monkeypatch: pytest.MonkeyPatch,
    upstream: _ScriptedUpstream,
    *,
    process_side_effect: Callable[..., Any] | None = None,
) -> tuple[proxy_service.ProxyService, proxy_service._HTTPBridgeSession, asyncio.Task[None], AsyncMock, list[int]]:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    session = _make_bridge_session(upstream)
    monkeypatch.setattr(proxy_service, "get_settings", _relay_settings)
    process_text = AsyncMock(side_effect=process_side_effect)
    monkeypatch.setattr(service, "_process_http_bridge_upstream_text", process_text)
    monkeypatch.setattr(service, "_retire_http_bridge_after_drain_if_ready", AsyncMock(return_value=False))
    snapshots: list[int] = []
    original_next_timeout = service._next_websocket_receive_timeout

    async def record_next_timeout(*args: Any, **kwargs: Any):
        snapshots.append(len(snapshots) + 1)
        return await original_next_timeout(*args, **kwargs)

    monkeypatch.setattr(service, "_next_websocket_receive_timeout", record_next_timeout)
    reader_task = asyncio.create_task(service._relay_http_bridge_upstream_messages(session))
    return service, session, reader_task, process_text, snapshots


async def _cancel_relay(reader_task: asyncio.Task[None]) -> None:
    reader_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reader_task


async def test_relay_loop_reuses_one_wakeup_waiter_across_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    message_count = 25
    upstream = _ScriptedUpstream(
        [f'{{"type":"response.output_text.delta","delta":"{index}"}}' for index in range(message_count)]
    )
    _service, session, reader_task, process_text, snapshots = _start_relay(monkeypatch, upstream)
    try:
        await asyncio.wait_for(upstream.blocked.wait(), timeout=1.0)
        await _wait_until(lambda: process_text.await_count == message_count)
        wakeup = cast(_CountingWakeupEvent, session.upstream_reader_wakeup)
        # One deadline snapshot per relayed message plus the final wait, but a
        # single wakeup waiter for the whole stream: the un-fired waiter stays
        # valid across iterations instead of being cancelled and re-created.
        assert len(snapshots) == message_count + 1
        assert wakeup.wait_calls == 1
        assert wakeup.wait_cancellations == 0
        assert upstream.receive_calls == message_count + 1
    finally:
        await _cancel_relay(reader_task)


async def test_relay_loop_exit_cancels_persistent_wakeup_waiter(monkeypatch: pytest.MonkeyPatch) -> None:
    upstream = _ScriptedUpstream([])
    _service, session, reader_task, _process_text, _snapshots = _start_relay(monkeypatch, upstream)
    wakeup = cast(_CountingWakeupEvent, session.upstream_reader_wakeup)
    await asyncio.wait_for(upstream.blocked.wait(), timeout=1.0)
    await _wait_until(lambda: wakeup.wait_calls == 1)

    await _cancel_relay(reader_task)

    assert wakeup.wait_cancellations == 1
    assert upstream.receive_cancellations == 1
    assert session.closed is True
    # No relay child survived the loop: the persistent waiter is not leaked.
    assert not [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task() and not task.done() and "Event.wait" in repr(task.get_coro())
    ]


async def test_send_during_processing_is_consumed_and_later_send_wakes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    upstream = _ScriptedUpstream(['{"type":"response.output_text.delta","delta":"a"}'])
    session_holder: list[proxy_service._HTTPBridgeSession] = []

    async def process_and_send(session: proxy_service._HTTPBridgeSession, text: str) -> None:
        # request_submit sets the event after a send; emulate a send landing
        # while the reader is still inside message processing.
        session.upstream_reader_wakeup.set()
        await asyncio.sleep(0)

    _service, session, reader_task, process_text, snapshots = _start_relay(
        monkeypatch, upstream, process_side_effect=process_and_send
    )
    session_holder.append(session)
    wakeup = cast(_CountingWakeupEvent, session.upstream_reader_wakeup)
    try:
        await asyncio.wait_for(upstream.blocked.wait(), timeout=1.0)
        await _wait_until(lambda: process_text.await_count == 1 and wakeup.wait_calls == 2)
        for _ in range(5):
            await asyncio.sleep(0)
        # The mid-processing set() completed the first waiter; the loop
        # consumed it at the top of the next iteration (that send is already
        # represented in the snapshot) and armed exactly one fresh waiter
        # without a spurious extra deadline evaluation.
        assert len(snapshots) == 2
        assert wakeup.wait_calls == 2
        assert wakeup.is_set() is False

        # A send that lands while the loop is waiting re-evaluates the
        # deadline exactly once and re-arms the waiter.
        session.upstream_reader_wakeup.set()
        await _wait_until(lambda: len(snapshots) == 3 and wakeup.wait_calls == 3)
        for _ in range(5):
            await asyncio.sleep(0)
        assert len(snapshots) == 3
        assert wakeup.wait_calls == 3
        assert upstream.receive_calls == 2
    finally:
        await _cancel_relay(reader_task)


async def test_relay_loop_processes_messages_in_order_without_wakeup_churn(monkeypatch: pytest.MonkeyPatch) -> None:
    texts = [f'{{"type":"response.output_text.delta","delta":"{index}"}}' for index in range(5)]
    upstream = _ScriptedUpstream(list(texts))
    _service, _session, reader_task, process_text, _snapshots = _start_relay(monkeypatch, upstream)
    try:
        await asyncio.wait_for(upstream.blocked.wait(), timeout=1.0)
        await _wait_until(lambda: process_text.await_count == len(texts))
        assert [call.args[1] for call in process_text.await_args_list] == texts
    finally:
        await _cancel_relay(reader_task)
