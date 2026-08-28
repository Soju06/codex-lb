from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from types import SimpleNamespace
from typing import Any, Callable, cast
from unittest.mock import AsyncMock, Mock

import anyio
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.clients.proxy_websocket import UpstreamWebSocket
from app.db.models import AccountStatus, Base
from app.modules.api_keys.service import ApiKeyData, ApiKeyUsageReservationData
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.http_bridge import upstream_events as http_bridge_upstream_events
from app.modules.proxy.durable_bridge_coordinator import DurableBridgeSessionCoordinator

pytestmark = pytest.mark.unit


def _make_http_bridge_session(
    pending_requests: deque[proxy_service._WebSocketRequestState],
    *,
    queued_request_count: int,
    key: proxy_service._HTTPBridgeSessionKey | None = None,
) -> proxy_service._HTTPBridgeSession:
    session_key = key or proxy_service._HTTPBridgeSessionKey("session_header", "sid-cancel-drain", None)
    return proxy_service._HTTPBridgeSession(
        key=session_key,
        headers={"x-codex-session-id": "sid-cancel-drain"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-cancel-drain",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.5",
        account=cast(Any, SimpleNamespace(id="acc-cancel-drain", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=queued_request_count,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )


def _make_request_state(
    request_id: str,
    *,
    response_id: str | None,
    awaiting_response_created: bool,
    event_queue: asyncio.Queue[str | None] | None = None,
) -> proxy_service._WebSocketRequestState:
    return proxy_service._WebSocketRequestState(
        request_id=request_id,
        model="gpt-5.5",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        response_id=response_id,
        awaiting_response_created=awaiting_response_created,
        event_queue=event_queue,
        transport="http",
        skip_request_log=True,
    )


def _make_api_key() -> ApiKeyData:
    return ApiKeyData(
        id="key-cancel-settle",
        name="cancel settle",
        key_prefix="sk-test",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=proxy_service.utcnow(),
        last_used_at=None,
    )


@pytest.mark.asyncio
async def test_cancelled_stream_settlement_task_releases_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, SimpleNamespace()))
    scheduled: list[tuple[str, str]] = []
    cleanup_tasks: list[asyncio.Task[None]] = []
    release_retry_flags: list[bool] = []

    async def release_unsettled(
        *,
        api_key: ApiKeyData,
        api_key_reservation: ApiKeyUsageReservationData,
        request_id: str,
        retry_persistence_failures: bool = False,
    ) -> None:
        scheduled.append((api_key.id, api_key_reservation.reservation_id))
        release_retry_flags.append(retry_persistence_failures)

    def schedule_cleanup(
        coro: Any,
        *,
        action: str,
        request_id: str,
    ) -> None:
        scheduled.append((action, request_id))
        cleanup_tasks.append(asyncio.create_task(coro))

    monkeypatch.setattr(service, "_release_unsettled_stream_api_key_usage", release_unsettled)
    monkeypatch.setattr(service, "_schedule_cancel_safe_cleanup", schedule_cleanup)

    task: asyncio.Task[bool] = asyncio.create_task(asyncio.sleep(60, result=True))
    service._track_stream_usage_settlement_task(
        task,
        api_key=_make_api_key(),
        api_key_reservation=ApiKeyUsageReservationData(
            reservation_id="res-cancel-settle",
            key_id="key-cancel-settle",
            model="gpt-5.5",
        ),
        request_id="req-cancel-settle",
    )
    task.cancel()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    if cleanup_tasks:
        await asyncio.gather(*cleanup_tasks)

    assert ("release_stream_api_key_reservation_after_cancelled_settlement", "req-cancel-settle") in scheduled
    assert ("key-cancel-settle", "res-cancel-settle") in scheduled
    assert release_retry_flags == [True]


@pytest.mark.asyncio
async def test_cancelled_http_bridge_request_retires_session_before_retry_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled downstream stream is an upstream ownership barrier.

    Rather than guessing whether later anonymous frames belong to the cancelled
    upstream response or to a retry, the bridge retires the shared upstream so a
    follow-up request is forced onto a fresh bridge/session path.
    """
    service = proxy_service.ProxyService(cast(Any, SimpleNamespace()))
    release_reservation = AsyncMock()
    monkeypatch.setattr(service, "_release_websocket_reservation", release_reservation)

    cancelled_request = _make_request_state(
        "req-cancelled",
        response_id="resp-cancelled",
        awaiting_response_created=False,
        event_queue=asyncio.Queue(),
    )
    session = _make_http_bridge_session(deque([cancelled_request]), queued_request_count=1)
    upstream_close = cast(Any, session.upstream).close

    detached = await service._detach_http_bridge_request(session, request_state=cancelled_request)

    assert detached is True
    assert cancelled_request.draining_until_terminal is True
    assert cancelled_request.event_queue is None
    assert session.queued_request_count == 0
    assert not session.pending_requests
    assert session.upstream_control.reconnect_requested is True
    assert session.upstream_control.retire_after_drain is True
    assert session.closed is True
    upstream_close.assert_awaited_once()
    release_reservation.assert_awaited_once_with(cancelled_request.api_key_reservation)


@pytest.mark.asyncio
async def test_http_bridge_detach_revokes_queue_before_releasing_pending_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, SimpleNamespace()))
    monkeypatch.setattr(service, "_release_websocket_reservation", AsyncMock())
    request_state = _make_request_state(
        "req-detach-lock",
        response_id="resp-detach-lock",
        awaiting_response_created=False,
        event_queue=asyncio.Queue(),
    )
    session = _make_http_bridge_session(deque([request_state]), queued_request_count=1)
    observations: list[bool] = []
    backing_lock = anyio.Lock()

    class ObservedPendingLock:
        async def __aenter__(self) -> ObservedPendingLock:
            await backing_lock.acquire()
            return self

        async def __aexit__(self, *args: object) -> None:
            if request_state not in session.pending_requests:
                observations.append(request_state.event_queue is None)
            backing_lock.release()

    session.pending_lock = cast(Any, ObservedPendingLock())

    assert await service._detach_http_bridge_request(session, request_state=request_state) is True
    assert observations
    assert observations[0] is True


@pytest.mark.parametrize("terminal_outcome", ["completed", "error"])
@pytest.mark.asyncio
async def test_http_bridge_stream_waits_only_while_completed_delivery_is_active(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    terminal_outcome: str,
) -> None:
    caplog.set_level(logging.INFO, logger="app.modules.proxy.service")
    service = proxy_service.ProxyService(cast(Any, SimpleNamespace()))
    queue_waiting = asyncio.Event()
    terminal_claimed = asyncio.Event()
    release_terminal = asyncio.Event()
    parse_sse_data_json = Mock(wraps=http_bridge_upstream_events.parse_sse_data_json)
    parse_sse_event_payload = Mock(wraps=http_bridge_upstream_events.parse_sse_event_payload)
    monkeypatch.setattr(http_bridge_upstream_events, "parse_sse_data_json", parse_sse_data_json)
    monkeypatch.setattr(http_bridge_upstream_events, "parse_sse_event_payload", parse_sse_event_payload)

    class ObservedQueue(asyncio.Queue[str | None]):
        async def get(self) -> str | None:
            queue_waiting.set()
            return await super().get()

    event_queue = ObservedQueue()
    request_state = _make_request_state(
        "req-terminal-race",
        response_id="resp-terminal-race",
        awaiting_response_created=False,
        event_queue=event_queue,
    )
    session = _make_http_bridge_session(deque(), queued_request_count=0)

    async def fake_submit_http_bridge_request(
        target_session: proxy_service._HTTPBridgeSession,
        *,
        request_state: proxy_service._WebSocketRequestState,
        text_data: str,
        queue_limit: int,
    ) -> None:
        del text_data, queue_limit
        async with target_session.pending_lock:
            target_session.pending_requests.append(request_state)
            target_session.queued_request_count += 1

    async def block_after_terminal_claim(*args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        assert request_state not in session.pending_requests
        terminal_claimed.set()
        await release_terminal.wait()
        if terminal_outcome == "error":
            raise RuntimeError("terminal persistence failed")
        return True

    finalize_request = AsyncMock()
    monkeypatch.setattr(service, "_submit_http_bridge_request", fake_submit_http_bridge_request)
    monkeypatch.setattr(service, "_register_http_bridge_previous_response_id", block_after_terminal_claim)
    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize_request)
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: SimpleNamespace(
            sse_keepalive_interval_seconds=0.001,
            stream_idle_timeout_seconds=0.002,
        ),
    )
    monkeypatch.setattr(proxy_service, "_HTTP_BRIDGE_STARTUP_KEEPALIVE_GRACE_SECONDS", 0.001)
    monkeypatch.setattr(proxy_service, "_STREAM_KEEPALIVE_MAX_COUNT", 1)

    async def consume_stream() -> list[str]:
        return [
            event_block
            async for event_block in service._stream_http_bridge_session_events(
                session,
                request_state=request_state,
                text_data="{}",
                queue_limit=8,
                propagate_http_errors=False,
                downstream_turn_state=None,
            )
        ]

    stream_task = asyncio.create_task(consume_stream())
    await asyncio.wait_for(queue_waiting.wait(), timeout=1.0)
    terminal_text = '{"type":"response.completed","response":{"id":"resp-terminal-race","status":"completed"}}'
    terminal_task = asyncio.create_task(service._process_http_bridge_upstream_text(session, terminal_text))
    await asyncio.wait_for(terminal_claimed.wait(), timeout=1.0)
    assert request_state.completed_delivery_scope is not None
    assert request_state.completed_delivery_scope.active is True
    assert await service._detach_http_bridge_request(session, request_state=request_state) is False
    assert request_state.event_queue is None
    await asyncio.sleep(0.02)
    assert not stream_task.done()

    release_terminal.set()
    if terminal_outcome == "completed":
        await asyncio.wait_for(terminal_task, timeout=1.0)
    else:
        with pytest.raises(RuntimeError, match="terminal persistence failed"):
            await asyncio.wait_for(terminal_task, timeout=1.0)
    event_blocks = await asyncio.wait_for(stream_task, timeout=1.0)

    event_types = [
        payload["type"]
        for event_block in event_blocks
        if isinstance(payload := proxy_service.parse_sse_data_json(event_block), dict)
    ]
    if terminal_outcome == "completed":
        assert event_types[-1] == "response.completed"
        assert event_types.count("response.completed") == 1
        assert "response.failed" not in event_types
        finalize_request.assert_awaited_once()
    else:
        assert event_types[-1] == "response.failed"
        assert "stream_idle_timeout" in "".join(event_blocks)
        finalize_request.assert_not_awaited()
    assert request_state.completed_delivery_scope is not None
    assert request_state.completed_delivery_scope.active is False
    assert parse_sse_data_json.call_count == 1
    assert parse_sse_event_payload.call_count == 1
    suppression_messages = [
        record.getMessage()
        for record in caplog.records
        if "HTTP bridge stream idle timeout suppressed during completed delivery" in record.getMessage()
    ]
    assert len(suppression_messages) == 1
    assert "request_id=req-terminal-race" in suppression_messages[0]
    assert "response_id=resp-terminal-race" in suppression_messages[0]
    assert "elapsed_seconds=" in suppression_messages[0]


def _attach_reservation_with_heartbeat(
    service: proxy_service.ProxyService,
    request_state: proxy_service._WebSocketRequestState,
    *,
    reservation_id: str,
) -> asyncio.Task[None]:
    request_state.api_key = _make_api_key()
    request_state.api_key_reservation = ApiKeyUsageReservationData(
        reservation_id=reservation_id,
        key_id="key-cancel-settle",
        model="gpt-5.5",
    )
    service._start_request_state_api_key_reservation_heartbeat(
        request_state,
        api_key=request_state.api_key,
        surface="http_bridge",
    )
    heartbeat_task = request_state.api_key_reservation_heartbeat_task
    assert heartbeat_task is not None
    return heartbeat_task


async def _assert_heartbeat_finished(heartbeat_task: asyncio.Task[None]) -> None:
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(heartbeat_task, timeout=1.0)
    assert heartbeat_task.done()


@pytest.mark.parametrize("abort_kind", ["exception", "cancellation"])
@pytest.mark.asyncio
async def test_http_bridge_aborted_completed_bookkeeping_settles_reservation(
    monkeypatch: pytest.MonkeyPatch,
    abort_kind: str,
) -> None:
    """Issue #1594: an abort after the completed pending pop must still settle.

    Once completed-event processing pops the terminal request from
    ``session.pending_requests``, neither the reader failure path nor the
    downstream detach reaches it any more. An exception or cancellation in the
    bookkeeping continuation must cancel the reservation heartbeat and release
    the API-key reservation exactly once instead of leaking both forever.
    """
    service = proxy_service.ProxyService(cast(Any, SimpleNamespace()))
    release_reservation = AsyncMock()
    monkeypatch.setattr(service, "_release_websocket_reservation", release_reservation)
    touch_reservation = AsyncMock(return_value=1.0)
    monkeypatch.setattr(service, "_maybe_touch_api_key_reservation", touch_reservation)

    event_queue: asyncio.Queue[str | None] = asyncio.Queue()
    request_state = _make_request_state(
        "req-aborted-completed",
        response_id="resp-aborted-completed",
        awaiting_response_created=False,
        event_queue=event_queue,
    )
    heartbeat_task = _attach_reservation_with_heartbeat(
        service,
        request_state,
        reservation_id="res-aborted-completed",
    )
    reservation = request_state.api_key_reservation
    session = _make_http_bridge_session(deque([request_state]), queued_request_count=1)

    bookkeeping_started = asyncio.Event()

    async def abort_bookkeeping(*args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        assert request_state not in session.pending_requests
        assert request_state.terminal_settlement_phase == "claimed"
        bookkeeping_started.set()
        if abort_kind == "exception":
            raise RuntimeError("continuity bookkeeping failed")
        await asyncio.Event().wait()
        return True

    finalize_request = AsyncMock()
    monkeypatch.setattr(service, "_register_http_bridge_previous_response_id", abort_bookkeeping)
    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize_request)

    terminal_text = '{"type":"response.completed","response":{"id":"resp-aborted-completed","status":"completed"}}'
    terminal_task = asyncio.create_task(service._process_http_bridge_upstream_text(session, terminal_text))
    await asyncio.wait_for(bookkeeping_started.wait(), timeout=1.0)
    if abort_kind == "exception":
        with pytest.raises(RuntimeError, match="continuity bookkeeping failed"):
            await asyncio.wait_for(terminal_task, timeout=1.0)
    else:
        terminal_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(terminal_task, timeout=1.0)

    release_reservation.assert_awaited_once_with(reservation)
    assert request_state.api_key_reservation is None
    assert request_state.terminal_settlement_phase is None
    await _assert_heartbeat_finished(heartbeat_task)
    assert request_state.api_key_reservation_heartbeat_task is None
    touch_reservation.assert_not_awaited()
    finalize_request.assert_not_awaited()
    # The downstream waiter is unblocked instead of waiting for idle timeout.
    assert event_queue.get_nowait() is None


@pytest.mark.asyncio
async def test_http_bridge_aborted_grouped_previous_response_bookkeeping_settles_remainder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #1594, grouped variant: abort mid-loop settles the popped remainder."""
    service = proxy_service.ProxyService(cast(Any, SimpleNamespace()))
    release_reservation = AsyncMock()
    monkeypatch.setattr(service, "_release_websocket_reservation", release_reservation)

    request_states = []
    heartbeat_tasks = []
    for index in range(2):
        request_state = _make_request_state(
            f"req-grouped-{index}",
            response_id=None,
            awaiting_response_created=True,
            event_queue=asyncio.Queue(),
        )
        request_state.previous_response_id = "resp-anchor"
        heartbeat_tasks.append(
            _attach_reservation_with_heartbeat(
                service,
                request_state,
                reservation_id=f"res-grouped-{index}",
            )
        )
        request_states.append(request_state)
    reservations = [request_state.api_key_reservation for request_state in request_states]
    session = _make_http_bridge_session(deque(request_states), queued_request_count=2)

    finalize_request = AsyncMock(side_effect=[None, RuntimeError("grouped finalize failed")])
    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize_request)

    terminal_text = (
        '{"type":"error","error_type":"invalid_request_error","code":"previous_response_not_found",'
        '"message":"Previous response with id \'resp-anchor\' not found.","param":"previous_response_id"}'
    )
    with pytest.raises(RuntimeError, match="grouped finalize failed"):
        await service._process_http_bridge_upstream_text(session, terminal_text)

    assert finalize_request.await_count == 2
    assert not session.pending_requests
    assert release_reservation.await_count == 2
    released = {call.args[0].reservation_id for call in release_reservation.await_args_list}
    assert released == {reservation.reservation_id for reservation in reservations if reservation is not None}
    for request_state, heartbeat_task in zip(request_states, heartbeat_tasks, strict=True):
        assert request_state.api_key_reservation is None
        assert request_state.terminal_settlement_phase is None
        await _assert_heartbeat_finished(heartbeat_task)


@pytest.mark.asyncio
async def test_http_bridge_detach_reclaims_abandoned_terminal_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #1594 backstop: detach settles a claim whose abort settlement failed."""
    service = proxy_service.ProxyService(cast(Any, SimpleNamespace()))
    release_reservation = AsyncMock()
    monkeypatch.setattr(service, "_release_websocket_reservation", release_reservation)

    request_state = _make_request_state(
        "req-abandoned-detach",
        response_id="resp-abandoned-detach",
        awaiting_response_created=False,
        event_queue=None,
    )
    heartbeat_task = _attach_reservation_with_heartbeat(
        service,
        request_state,
        reservation_id="res-abandoned-detach",
    )
    reservation = request_state.api_key_reservation
    session = _make_http_bridge_session(deque(), queued_request_count=0)

    # A live claim still belongs to the bookkeeping continuation: detach must
    # not settle it out from under an in-flight finalize.
    request_state.terminal_settlement_phase = "claimed"
    assert await service._detach_http_bridge_request(session, request_state=request_state) is False
    release_reservation.assert_not_awaited()
    assert request_state.terminal_settlement_phase == "claimed"

    # An abandoned claim (abort settlement failed) has no owner left; detach
    # is the backstop that reclaims settlement.
    request_state.terminal_settlement_phase = "abandoned"
    assert await service._detach_http_bridge_request(session, request_state=request_state) is False
    release_reservation.assert_awaited_once_with(reservation)
    assert request_state.api_key_reservation is None
    assert request_state.terminal_settlement_phase is None
    await _assert_heartbeat_finished(heartbeat_task)


@pytest.mark.asyncio
async def test_http_bridge_stream_idle_timeout_revokes_queue_before_completed_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, SimpleNamespace()))
    queue_waiting = asyncio.Event()

    class ObservedQueue(asyncio.Queue[str | None]):
        async def get(self) -> str | None:
            queue_waiting.set()
            return await super().get()

    event_queue = ObservedQueue()
    request_state = _make_request_state(
        "req-timeout-first",
        response_id="resp-timeout-first",
        awaiting_response_created=False,
        event_queue=event_queue,
    )
    session = _make_http_bridge_session(deque(), queued_request_count=0)
    backing_lock = anyio.Lock()
    queue_revoked_before_lock_release = False

    class ObservedPendingLock:
        async def __aenter__(self) -> ObservedPendingLock:
            await backing_lock.acquire()
            return self

        async def __aexit__(self, *args: object) -> None:
            nonlocal queue_revoked_before_lock_release
            if request_state.event_queue is None:
                queue_revoked_before_lock_release = True
            backing_lock.release()

    session.pending_lock = cast(Any, ObservedPendingLock())

    async def fake_submit_http_bridge_request(
        target_session: proxy_service._HTTPBridgeSession,
        *,
        request_state: proxy_service._WebSocketRequestState,
        text_data: str,
        queue_limit: int,
    ) -> None:
        del text_data, queue_limit
        async with target_session.pending_lock:
            target_session.pending_requests.append(request_state)
            target_session.queued_request_count += 1

    async def fake_detach_http_bridge_request(
        target_session: proxy_service._HTTPBridgeSession,
        *,
        request_state: proxy_service._WebSocketRequestState,
    ) -> bool:
        del target_session
        assert request_state.event_queue is None
        return False

    finalize_request = AsyncMock()
    monkeypatch.setattr(service, "_submit_http_bridge_request", fake_submit_http_bridge_request)
    monkeypatch.setattr(service, "_detach_http_bridge_request", fake_detach_http_bridge_request)
    monkeypatch.setattr(service, "_register_http_bridge_previous_response_id", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize_request)
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: SimpleNamespace(
            sse_keepalive_interval_seconds=0.001,
            stream_idle_timeout_seconds=0.001,
        ),
    )
    monkeypatch.setattr(proxy_service, "_HTTP_BRIDGE_STARTUP_KEEPALIVE_GRACE_SECONDS", 0.001)
    monkeypatch.setattr(proxy_service, "_STREAM_KEEPALIVE_MAX_COUNT", 1)

    async def consume_stream() -> list[str]:
        return [
            event_block
            async for event_block in service._stream_http_bridge_session_events(
                session,
                request_state=request_state,
                text_data="{}",
                queue_limit=8,
                propagate_http_errors=False,
                downstream_turn_state=None,
            )
        ]

    stream_task = asyncio.create_task(consume_stream())
    await asyncio.wait_for(queue_waiting.wait(), timeout=1.0)
    event_blocks = await asyncio.wait_for(stream_task, timeout=1.0)

    assert queue_revoked_before_lock_release is True
    assert request_state.event_queue is None
    assert request_state in session.pending_requests
    assert "stream_idle_timeout" in "".join(event_blocks)

    terminal_text = '{"type":"response.completed","response":{"id":"resp-timeout-first","status":"completed"}}'
    await service._process_http_bridge_upstream_text(session, terminal_text)

    assert request_state.completed_delivery_scope is None
    assert event_queue.empty()
    finalize_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_http_bridge_completed_delivery_stays_dominant_after_recovery_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, SimpleNamespace()))
    recovery_started = asyncio.Event()
    release_recovery = asyncio.Event()
    event_queue: asyncio.Queue[str | None] = asyncio.Queue()
    request_state = _make_request_state(
        "req-completed-during-recovery",
        response_id=None,
        awaiting_response_created=True,
        event_queue=event_queue,
    )
    session = _make_http_bridge_session(deque(), queued_request_count=0)

    async def fake_submit_http_bridge_request(
        target_session: proxy_service._HTTPBridgeSession,
        *,
        request_state: proxy_service._WebSocketRequestState,
        text_data: str,
        queue_limit: int,
    ) -> None:
        del text_data, queue_limit
        async with target_session.pending_lock:
            target_session.pending_requests.append(request_state)
            target_session.queued_request_count += 1

    async def block_idle_recovery(*args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        recovery_started.set()
        await release_recovery.wait()
        return False

    finalize_request = AsyncMock()
    monkeypatch.setattr(service, "_submit_http_bridge_request", fake_submit_http_bridge_request)
    monkeypatch.setattr(service, "_retry_http_bridge_precreated_request", block_idle_recovery)
    monkeypatch.setattr(service, "_http_bridge_precreated_retry_cooldown_seconds", AsyncMock(return_value=0.0))
    monkeypatch.setattr(service, "_register_http_bridge_previous_response_id", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "_record_http_bridge_retry_circuit_failure", AsyncMock())
    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize_request)
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: SimpleNamespace(
            sse_keepalive_interval_seconds=0.001,
            stream_idle_timeout_seconds=0.001,
        ),
    )
    monkeypatch.setattr(proxy_service, "_HTTP_BRIDGE_STARTUP_KEEPALIVE_GRACE_SECONDS", 0.001)
    monkeypatch.setattr(proxy_service, "_STREAM_KEEPALIVE_MAX_COUNT", 1)

    async def consume_stream() -> list[str]:
        return [
            event_block
            async for event_block in service._stream_http_bridge_session_events(
                session,
                request_state=request_state,
                text_data="{}",
                queue_limit=8,
                propagate_http_errors=False,
                downstream_turn_state=None,
            )
        ]

    stream_task = asyncio.create_task(consume_stream())
    await asyncio.wait_for(recovery_started.wait(), timeout=1.0)

    terminal_text = (
        '{"type":"response.completed","response":{"id":"resp-completed-during-recovery","status":"completed"}}'
    )
    await service._process_http_bridge_upstream_text(session, terminal_text)

    assert request_state.completed_delivery_scope is not None
    assert request_state.completed_delivery_scope.active is False
    assert request_state.completed_delivery_scope.terminal_enqueued is True
    release_recovery.set()
    event_blocks = await asyncio.wait_for(stream_task, timeout=1.0)

    event_types = [
        payload["type"]
        for event_block in event_blocks
        if isinstance(payload := proxy_service.parse_sse_data_json(event_block), dict)
    ]
    assert event_types[-1] == "response.completed"
    assert event_types.count("response.completed") == 1
    assert "response.failed" not in event_types
    finalize_request.assert_awaited_once()


def test_retiring_http_bridge_session_is_not_reusable() -> None:
    session = _make_http_bridge_session(deque(), queued_request_count=0)
    session.upstream_control.retire_after_drain = True

    assert not proxy_service._http_bridge_session_reusable_for_request(
        session=session,
        key=session.key,
        incoming_turn_state=None,
        previous_response_id=None,
    )


@pytest.mark.asyncio
async def test_retiring_http_bridge_session_is_not_live_for_anchor_decision() -> None:
    service = proxy_service.ProxyService(cast(Any, SimpleNamespace()))
    key = proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_retiring", None)
    session = _make_http_bridge_session(deque(), queued_request_count=0, key=key)
    session.upstream_control.retire_after_drain = True

    async with service._http_bridge_lock:
        service._http_bridge_sessions[key] = session

    assert not await service._http_bridge_has_live_local_session(
        key=key,
        incoming_turn_state="http_turn_retiring",
        api_key=None,
    )

    session.upstream_control.retire_after_drain = False

    assert await service._http_bridge_has_live_local_session(
        key=key,
        incoming_turn_state="http_turn_retiring",
        api_key=None,
    )


@pytest.mark.asyncio
async def test_retiring_http_bridge_session_stays_live_while_visible_request_finishes() -> None:
    service = proxy_service.ProxyService(cast(Any, SimpleNamespace()))
    key = proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_retiring_visible", None)
    visible_request = _make_request_state(
        "req-visible",
        response_id="resp-visible",
        awaiting_response_created=False,
        event_queue=asyncio.Queue(),
    )
    session = _make_http_bridge_session(deque([visible_request]), queued_request_count=1, key=key)
    session.upstream_control.retire_after_drain = True

    async with service._http_bridge_lock:
        service._http_bridge_sessions[key] = session

    assert proxy_service._http_bridge_session_retiring_with_visible_requests(session)
    assert await service._http_bridge_has_live_local_session(
        key=key,
        incoming_turn_state="http_turn_retiring_visible",
        api_key=None,
    )


@pytest.mark.asyncio
async def test_detached_retiring_session_does_not_alias_completed_response_to_replacement() -> None:
    service = proxy_service.ProxyService(cast(Any, SimpleNamespace()))
    key = proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_replaced", None)
    old_request = _make_request_state(
        "req-old-visible",
        response_id="resp-old-visible",
        awaiting_response_created=False,
        event_queue=asyncio.Queue(),
    )
    old_session = _make_http_bridge_session(deque([old_request]), queued_request_count=1, key=key)
    old_session.upstream_control.retire_after_drain = True
    replacement_session = _make_http_bridge_session(deque(), queued_request_count=0, key=key)

    async with service._http_bridge_lock:
        service._http_bridge_sessions[key] = replacement_session

    await service._register_http_bridge_previous_response_id(old_session, "resp-old-completed")

    alias_key = proxy_service._http_bridge_previous_response_alias_key("resp-old-completed", key.api_key_id)
    assert alias_key not in service._http_bridge_previous_response_index
    assert old_session.previous_response_ids == set()


def test_response_created_prefers_visible_request_when_drain_and_visible_overlap() -> None:
    draining_request = _make_request_state(
        "req-cancelled-before-created",
        response_id=None,
        awaiting_response_created=True,
    )
    draining_request.draining_until_terminal = True
    active_request = _make_request_state(
        "req-active-created",
        response_id=None,
        awaiting_response_created=True,
    )

    matched_request = proxy_service._assign_websocket_response_id(
        deque([draining_request, active_request]),
        "resp-visible-created",
    )

    assert matched_request is active_request
    assert draining_request.response_id is None
    assert active_request.response_id == "resp-visible-created"


def test_response_created_prefers_draining_owner_when_no_visible_request() -> None:
    draining_request = _make_request_state(
        "req-cancelled-before-created",
        response_id=None,
        awaiting_response_created=True,
    )
    draining_request.draining_until_terminal = True

    matched_request = proxy_service._assign_websocket_response_id(
        deque([draining_request]),
        "resp-late-cancelled",
    )

    assert matched_request is draining_request
    assert draining_request.response_id == "resp-late-cancelled"


def test_anonymous_event_prefers_active_request_over_draining_owner_in_illegal_overlap() -> None:
    draining_request = _make_request_state(
        "req-cancelled-draining",
        response_id="resp-cancelled-draining",
        awaiting_response_created=False,
        event_queue=None,
    )
    draining_request.draining_until_terminal = True
    active_request = _make_request_state(
        "req-active-delta",
        response_id="resp-active-delta",
        awaiting_response_created=False,
        event_queue=asyncio.Queue(),
    )

    matched_request = proxy_service._match_websocket_request_state_for_anonymous_event(
        deque([draining_request, active_request]),
        prefer_previous_response_not_found=False,
        prefer_draining_requests=True,
    )

    assert matched_request is active_request


def test_anonymous_event_prefers_unresolved_draining_owner_before_visible_retry() -> None:
    draining_request = _make_request_state(
        "req-cancelled-before-created",
        response_id=None,
        awaiting_response_created=True,
        event_queue=None,
    )
    draining_request.draining_until_terminal = True
    retry_request = _make_request_state(
        "req-visible-retry",
        response_id=None,
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
    )

    matched_request = proxy_service._match_websocket_request_state_for_anonymous_event(
        deque([draining_request, retry_request]),
        prefer_previous_response_not_found=False,
        prefer_draining_requests=True,
    )

    assert matched_request is draining_request


def test_anonymous_event_prefers_unresolved_visible_request_before_active_response() -> None:
    """A normal pipelined request awaiting response.created owns pre-created anonymous events."""
    active_request = _make_request_state(
        "req-active-created",
        response_id="resp-active-created",
        awaiting_response_created=False,
        event_queue=asyncio.Queue(),
    )
    waiting_request = _make_request_state(
        "req-waiting-created",
        response_id=None,
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
    )

    matched_request = proxy_service._match_websocket_request_state_for_anonymous_event(
        deque([active_request, waiting_request]),
        prefer_previous_response_not_found=False,
        prefer_draining_requests=True,
    )

    assert matched_request is waiting_request


def test_anonymous_terminal_errors_can_target_visible_retry_when_drain_exists() -> None:
    draining_request = _make_request_state(
        "req-cancelled-precreated",
        response_id=None,
        awaiting_response_created=True,
    )
    draining_request.draining_until_terminal = True
    retry_request = _make_request_state(
        "req-visible-retry",
        response_id=None,
        awaiting_response_created=True,
    )

    matched_request = proxy_service._match_websocket_request_state_for_anonymous_event(
        deque([draining_request, retry_request]),
        prefer_previous_response_not_found=False,
        prefer_draining_requests=False,
    )

    assert matched_request is retry_request


@pytest.mark.asyncio
async def test_response_created_does_not_promote_in_progress_durable_anchor() -> None:
    """Undo/edit safety: an in-progress response must not become the auto-continuation anchor.

    If turn D has only reached response.created when the client interrupts/edits,
    a later short E request on the same logical thread must not be auto-anchored
    to D. Only a completed response is safe as the durable latest_response_id.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    coordinator = DurableBridgeSessionCoordinator(cast(Callable[[], AsyncSession], session_factory))

    instance_id = proxy_service.get_settings().http_responses_session_bridge_instance_id
    lookup = await coordinator.claim_live_session(
        session_key_kind="turn_state_header",
        session_key_value="thread-undo-edit",
        api_key_id=None,
        instance_id=instance_id,
        owner_process_epoch="test-process",
        lease_ttl_seconds=60.0,
        account_id="acc-undo-edit",
        model="gpt-5.5",
        service_tier=None,
        latest_turn_state="thread-undo-edit",
        latest_response_id="resp_B_completed",
        allow_takeover=True,
    )

    service = proxy_service.ProxyService(cast(Any, SimpleNamespace()))
    service._durable_bridge = coordinator  # noqa: SLF001
    request_state = _make_request_state(
        "req-D-in-progress",
        response_id=None,
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
    )
    session = _make_http_bridge_session(deque([request_state]), queued_request_count=1)
    session.key = proxy_service._HTTPBridgeSessionKey("turn_state_header", "thread-undo-edit", None)
    session.headers = {"x-codex-turn-state": "thread-undo-edit"}
    session.durable_session_id = lookup.session_id
    session.durable_owner_epoch = lookup.owner_epoch

    await service._process_http_bridge_upstream_text(  # noqa: SLF001
        session,
        '{"type":"response.created","response":{"id":"resp_D_in_progress","object":"response","status":"in_progress"}}',
    )

    refreshed = await coordinator.lookup_request_targets(
        session_key_kind="turn_state_header",
        session_key_value="thread-undo-edit",
        api_key_id=None,
        turn_state="thread-undo-edit",
        session_header=None,
        previous_response_id=None,
    )

    assert refreshed is not None
    assert refreshed.latest_response_id == "resp_B_completed"
    await engine.dispose()
