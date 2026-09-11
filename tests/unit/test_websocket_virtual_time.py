"""Websocket turn-path waits are owned by the injected scheduler and clock.

Each test drives a ``websocket/mixin.py`` site that used to call ``asyncio`` /
``anyio`` / ``time`` directly through ``VirtualScheduler``: the site must park
on a virtual timer (no wall-clock sleep) and resume only when the simulation
advances the clock. Under the real defaults every one of these sites is the
same primitive call it was before the seam existed.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, AsyncIterator, Awaitable, Callable, cast

import anyio
import pytest
from fastapi import WebSocket

import app.modules.proxy._service.support as transport_health
from app.core.clients.proxy import ProxyResponseError
from app.core.clients.proxy_websocket import UpstreamWebSocket
from app.db.models import Account
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.websocket import mixin as websocket_mixin
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

pytestmark = pytest.mark.unit


class _RequestLogsRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def add_log(self, **kwargs: object) -> None:
        self.calls.append(dict(kwargs))


@asynccontextmanager
async def _repo_factory() -> AsyncIterator[SimpleNamespace]:
    yield SimpleNamespace(request_logs=_RequestLogsRecorder(), api_keys=object())


def _virtual_service() -> tuple[proxy_service.ProxyService, VirtualClock, VirtualScheduler]:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    service = proxy_service.ProxyService(
        cast(proxy_service.ProxyRepoFactory, _repo_factory),
        clock=clock,
        scheduler=scheduler,
    )
    return service, clock, scheduler


class _OpenHarness(websocket_mixin._WebSocketMixin):
    """Websocket mixin owner whose upstream connector is supplied by the test."""

    def __init__(
        self,
        clock: VirtualClock,
        scheduler: VirtualScheduler,
        opener: Callable[[], Awaitable[Any]],
    ) -> None:
        self._clock = clock
        self._scheduler = scheduler
        self._opener = opener

    async def _open_upstream_websocket(
        self,
        account: Any,
        headers: Any,
        *,
        request_state: Any = None,
        connect_progress: Any = None,
    ) -> Any:
        del account, headers, request_state
        if connect_progress is not None:
            connect_progress.direct_upstream_connect_started = True
        return await self._opener()


class _BlockingUpstream:
    def __init__(self) -> None:
        self.receive_calls = 0

    async def receive(self) -> SimpleNamespace:
        self.receive_calls += 1
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def close(self) -> None:
        return None


class _DownstreamWebSocket:
    def __init__(self) -> None:
        self.sent_text: list[str] = []
        self.closed = False

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)

    async def send_bytes(self, _data: bytes) -> None:
        return None

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        del code, reason
        self.closed = True


@pytest.mark.asyncio
async def test_upstream_websocket_open_budget_expires_on_the_virtual_scheduler() -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)

    async def stalled_open() -> Any:
        await asyncio.Event().wait()

    harness = _OpenHarness(clock, scheduler, stalled_open)
    account = cast(Account, SimpleNamespace(id="account-virtual-open"))
    opener = scheduler.create_task(harness._open_upstream_websocket_with_budget(account, {}, timeout_seconds=5.0))
    try:
        await scheduler.drain()
        assert not opener.done()
        # ``fail_after`` armed exactly one virtual timer instead of an anyio deadline.
        assert scheduler.pending_timers == 1

        await scheduler.advance(4.0)
        assert not opener.done()

        await scheduler.advance(1.0)
        with pytest.raises(ProxyResponseError) as exc_info:
            await opener

        assert proxy_service._is_proxy_budget_exhausted_error(exc_info.value)
        assert clock.monotonic() == pytest.approx(5.0)
        assert transport_health.upstream_websocket_transport_recently_failed() is True
        assert scheduler.pending_timers == 0
    finally:
        transport_health.clear_upstream_websocket_transport_failure()
        await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_upstream_websocket_open_within_budget_disarms_the_virtual_deadline() -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    upstream = object()

    async def slow_open() -> Any:
        await scheduler.sleep(1.0)
        return upstream

    harness = _OpenHarness(clock, scheduler, slow_open)
    account = cast(Account, SimpleNamespace(id="account-virtual-open-ok"))
    opener = scheduler.create_task(harness._open_upstream_websocket_with_budget(account, {}, timeout_seconds=5.0))
    try:
        await scheduler.drain()
        assert not opener.done()

        await scheduler.advance(1.0)

        assert await opener is upstream
        assert scheduler.pending_timers == 0
        assert transport_health.upstream_websocket_transport_recently_failed() is False
    finally:
        await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_upstream_relay_receive_deadline_and_keepalive_follow_the_virtual_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, clock, scheduler = _virtual_service()
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: SimpleNamespace(sse_keepalive_interval_seconds=10.0),
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req_virtual_relay",
        model="gpt-5.5",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=clock.monotonic(),
        response_id="resp_virtual_relay",
        response_create_sent_at=clock.monotonic(),
    )
    pending_requests: deque[proxy_service._WebSocketRequestState] = deque([request_state])
    upstream = _BlockingUpstream()
    downstream = _DownstreamWebSocket()

    relay = scheduler.create_task(
        service._relay_upstream_websocket_messages(
            cast(WebSocket, downstream),
            cast(UpstreamWebSocket, upstream),
            account=cast(Account, SimpleNamespace(id="account_virtual_relay")),
            account_id_value="account_virtual_relay",
            pending_requests=pending_requests,
            pending_lock=anyio.Lock(),
            client_send_lock=anyio.Lock(),
            api_key=None,
            upstream_control=proxy_service._WebSocketUpstreamControl(),
            response_create_gate=asyncio.Semaphore(1),
            proxy_request_budget_seconds=30.0,
            stream_idle_timeout_seconds=30.0,
            downstream_activity=proxy_service._DownstreamWebSocketActivity(),
        ),
        name="test-virtual-relay",
    )
    try:
        await scheduler.drain()
        assert not relay.done()
        assert downstream.sent_text == []
        assert upstream.receive_calls == 1
        # The receive deadline is a virtual timer, not a wall-clock wait_for.
        assert scheduler.pending_timers == 1

        await scheduler.advance(9.0)
        assert downstream.sent_text == []

        await scheduler.advance(1.0)

        assert [json.loads(text)["type"] for text in downstream.sent_text] == ["response.in_progress"]
        assert not relay.done()
        # The keepalive re-armed the receive wait instead of expiring the request budget.
        assert upstream.receive_calls == 2
        assert pending_requests == deque([request_state])
    finally:
        relay.cancel()
        await asyncio.gather(relay, return_exceptions=True)
        await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_owned_task_observation_reports_completion_within_the_virtual_deadline(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    monkeypatch.setattr(proxy_service, "_TASK_CANCEL_TIMEOUT_SECONDS", 0.01)

    async def failing_child() -> None:
        await scheduler.sleep(0.005)
        raise RuntimeError("owned child crashed")

    child = scheduler.create_task(failing_child())
    waiter = scheduler.create_task(
        websocket_mixin._await_owned_websocket_task_after_reader_cancellation(
            child,
            failure_message="owned child failed after reader cancellation",
            scheduler=scheduler,
        )
    )
    await scheduler.drain()
    assert not waiter.done()

    with caplog.at_level(logging.WARNING, logger=proxy_service.logger.name):
        await scheduler.advance(0.005)
        await waiter

    assert child.done()
    # Completion inside the deadline takes the ``done`` branch: the child's
    # failure is observed and logged instead of being reported as a timeout.
    assert "owned child failed after reader cancellation" in caplog.text
    assert scheduler.pending_timers == 0
