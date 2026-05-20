from __future__ import annotations

import asyncio
from collections import deque
from types import SimpleNamespace
from typing import Any, Callable, cast
from unittest.mock import AsyncMock

import anyio
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.clients.proxy_websocket import UpstreamResponsesWebSocket
from app.db.models import AccountStatus, Base
from app.modules.proxy import service as proxy_service
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
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
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
