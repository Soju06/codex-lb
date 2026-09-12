"""Handler-level coverage for the bounded terminal transcript write.

Every other test of the terminal spool drives the batcher or the repository
directly, which lets a fence pass in isolation while rejecting the ordering the
relay actually uses: ``_process_http_bridge_upstream_text`` publishes the
operation state (``completed``/``incomplete``) *before* appending the terminal
transcript block, so a terminal append always observes its own row as already
terminal with an incomplete spool. These tests drive the relay handler through
the real ``DurableBridgeRepository`` so that ordering is part of the contract.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.clients.proxy_websocket import UpstreamWebSocket
from app.db.models import AccountStatus, Base
from app.modules.proxy import service as proxy_service
from app.modules.proxy.durable_bridge_coordinator import DurableBridgeSessionCoordinator
from app.modules.proxy.durable_bridge_repository import durable_bridge_hash, durable_bridge_operation_id
from app.modules.proxy.http_bridge_event_batcher import HttpBridgeOperationEventBatcher


@pytest.fixture
async def async_session_factory() -> AsyncIterator[Callable[[], AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    def get_session() -> AsyncSession:
        return session_maker()

    yield get_session

    await engine.dispose()


def _make_bridge_session(
    *,
    key_value: str,
    pending_requests: deque[Any],
    queued_request_count: int,
) -> Any:
    session_key = proxy_service._HTTPBridgeSessionKey("session_header", key_value, None)  # noqa: SLF001
    return proxy_service._HTTPBridgeSession(  # noqa: SLF001
        key=session_key,
        headers={"x-codex-session-id": key_value},
        affinity=proxy_service._AffinityPolicy(  # noqa: SLF001
            key=key_value,
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.6-sol",
        account=cast(Any, SimpleNamespace(id="acc-terminal-spool", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),  # noqa: SLF001
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=queued_request_count,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )


async def _arrange_operation(
    async_session_factory: Callable[[], AsyncSession],
    *,
    key_value: str,
    response_id: str,
    terminal_append_timeout_seconds: float = 5.0,
) -> tuple[Any, Any, Any, DurableBridgeSessionCoordinator, str]:
    """Wire the relay handler to a real repository with one live operation."""
    coordinator = DurableBridgeSessionCoordinator(async_session_factory)
    instance_id = proxy_service.get_settings().http_responses_session_bridge_instance_id
    lookup = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value=key_value,
        api_key_id=None,
        instance_id=instance_id,
        owner_process_epoch="test-process",
        lease_ttl_seconds=60.0,
        account_id="acc-terminal-spool",
        model="gpt-5.6-sol",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    fingerprint = durable_bridge_hash(key_value)
    operation_id = durable_bridge_operation_id(lookup.session_id, fingerprint)
    operation = await coordinator.record_operation(
        operation_id=operation_id,
        session_id=lookup.session_id,
        instance_id=instance_id,
        owner_epoch=lookup.owner_epoch,
        request_fingerprint=fingerprint,
        account_id="acc-terminal-spool",
        model="gpt-5.6-sol",
        parent_response_id=None,
    )
    assert operation is not None

    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    service._durable_bridge = coordinator  # noqa: SLF001
    service._http_bridge_operation_event_batcher = HttpBridgeOperationEventBatcher(  # noqa: SLF001
        coordinator,
        max_bytes=64 * 1024,
        # The background flusher must not race the synchronous terminal drain.
        flush_interval_seconds=60.0,
        terminal_append_timeout_seconds=terminal_append_timeout_seconds,
    )

    request_state = proxy_service._WebSocketRequestState(  # noqa: SLF001
        request_id=f"req-{key_value}",
        response_id=response_id,
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        event_queue=asyncio.Queue(),
        transport="http",
        skip_request_log=True,
    )
    request_state.operation_id = operation_id
    session = _make_bridge_session(
        key_value=key_value,
        pending_requests=deque([request_state]),
        queued_request_count=1,
    )
    session.durable_session_id = lookup.session_id
    session.durable_owner_epoch = lookup.owner_epoch
    return service, session, request_state, coordinator, operation_id


async def _settle_detached_terminal_tasks(batcher: HttpBridgeOperationEventBatcher) -> None:
    """Await the batcher's detached terminal tasks instead of cancelling them.

    ``close()`` cancels them, which is right for shutdown but wrong here: the
    fenced finalization that flips ``event_spool_complete`` is scheduled as a
    detached task, and cancelling it mid-write would also invalidate the
    in-memory SQLite connection.
    """
    for _ in range(100):
        tasks = tuple(batcher._terminal_append_tasks) + tuple(batcher._terminal_finalize_tasks)  # noqa: SLF001
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)
    raise AssertionError("batcher terminal tasks did not settle")


def _completed_frame(response_id: str) -> str:
    return json.dumps(
        {
            "type": "response.completed",
            "response": {
                "id": response_id,
                "object": "response",
                "status": "completed",
                "output": [],
            },
        }
    )


@pytest.mark.asyncio
async def test_normal_completion_persists_replayable_terminal_transcript(
    async_session_factory: Callable[[], AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plain completion must leave the operation replayable.

    This is the regression that a batcher-only or repository-only test cannot
    express. The relay publishes ``state = completed`` before the terminal
    append, so any fence keyed on "terminal state with an incomplete spool"
    rejects every ordinary completion: the row ends ``completed`` with
    ``event_spool_complete = False`` and zero events, which disables transcript
    replay and hard continuity fleet-wide.
    """
    response_id = "resp-normal-completion"
    service, session, request_state, coordinator, operation_id = await _arrange_operation(
        async_session_factory,
        key_value="normal-completion",
        response_id=response_id,
    )
    monkeypatch.setattr(service, "_finalize_websocket_request_state", AsyncMock())

    await service._process_http_bridge_upstream_text(  # noqa: SLF001
        session,
        _completed_frame(response_id),
    )
    # The deferred fenced finalization is a detached task by design.
    await _settle_detached_terminal_tasks(service._http_bridge_operation_event_batcher)  # noqa: SLF001

    operation = await coordinator.get_operation(operation_id=operation_id)
    assert operation is not None
    assert operation.state == "completed"
    assert operation.response_id == response_id
    assert operation.event_spool_complete is True

    events = await coordinator.get_operation_events(operation_id=operation_id)
    assert len(events) == 1
    assert "response.completed" in events[0]


@pytest.mark.asyncio
async def test_stalled_terminal_append_settles_without_replay(
    async_session_factory: Callable[[], AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal append that misses its bound must settle, not replay.

    The complement of the test above: the fence has to stay effective. When the
    bounded append is abandoned, the fallback settlement publishes the terminal
    outcome, and the late append that finally reaches the writer must not
    rewrite the row or flip the spool back to replayable.
    """
    response_id = "resp-stalled-completion"
    service, session, request_state, coordinator, operation_id = await _arrange_operation(
        async_session_factory,
        key_value="stalled-completion",
        response_id=response_id,
        terminal_append_timeout_seconds=0.01,
    )
    monkeypatch.setattr(service, "_finalize_websocket_request_state", AsyncMock())

    release_append = asyncio.Event()
    append_started = asyncio.Event()
    real_append = coordinator.append_terminal_operation_event

    async def stalled_append(**kwargs: Any) -> bool:
        append_started.set()
        await release_append.wait()
        return await real_append(**kwargs)

    monkeypatch.setattr(coordinator, "append_terminal_operation_event", stalled_append)

    await service._process_http_bridge_upstream_text(  # noqa: SLF001
        session,
        _completed_frame(response_id),
    )

    assert append_started.is_set()
    settled = await coordinator.get_operation(operation_id=operation_id)
    assert settled is not None
    assert settled.state == "completed"
    assert settled.event_spool_complete is False

    # Release the abandoned append and let it run to completion: it must find
    # the row fenced rather than rewrite the settled outcome.
    release_append.set()
    await _settle_detached_terminal_tasks(service._http_bridge_operation_event_batcher)  # noqa: SLF001

    operation = await coordinator.get_operation(operation_id=operation_id)
    assert operation is not None
    assert operation.state == "completed"
    assert operation.event_spool_complete is False
    assert await coordinator.get_operation_events(operation_id=operation_id) == []

    # The terminal SSE block still reached the client.
    queued: list[str | None] = []
    while not request_state.event_queue.empty():
        queued.append(request_state.event_queue.get_nowait())
    assert any(item is not None and "response.completed" in item for item in queued)


@pytest.mark.asyncio
async def test_late_clear_does_not_strand_a_newer_terminal_attempt(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    """A timed-out append's cleanup must not erase a newer attempt's state.

    The abandoned append owns an in-memory context for the operation. When the
    durable layer finally releases it, its cleanup runs; by then a retry of the
    same operation may have registered its own context, and clearing it would
    make the retry give up its transcript and fall back to settlement.
    """
    coordinator = DurableBridgeSessionCoordinator(async_session_factory)
    instance_id = proxy_service.get_settings().http_responses_session_bridge_instance_id
    lookup = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="late-clear",
        api_key_id=None,
        instance_id=instance_id,
        owner_process_epoch="test-process",
        lease_ttl_seconds=60.0,
        account_id="acc-late-clear",
        model="gpt-5.6-sol",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    fingerprint = durable_bridge_hash("late-clear")
    operation_id = durable_bridge_operation_id(lookup.session_id, fingerprint)
    assert await coordinator.record_operation(
        operation_id=operation_id,
        session_id=lookup.session_id,
        instance_id=instance_id,
        owner_epoch=lookup.owner_epoch,
        request_fingerprint=fingerprint,
        account_id="acc-late-clear",
        model="gpt-5.6-sol",
        parent_response_id=None,
    )

    release_first_append = asyncio.Event()
    first_append_started = asyncio.Event()
    real_append = coordinator.append_terminal_operation_event
    attempts = 0

    async def gated_append(**kwargs: Any) -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            first_append_started.set()
            await release_first_append.wait()
        return await real_append(**kwargs)

    gated_coordinator = cast(
        Any,
        SimpleNamespace(
            append_terminal_operation_event=gated_append,
            append_operation_events=coordinator.append_operation_events,
            append_operation_event_chunk=coordinator.append_operation_event_chunk,
            append_terminal_operation_chunk=coordinator.append_terminal_operation_chunk,
            finalize_operation_event_spool=coordinator.finalize_operation_event_spool,
            settle_terminal_append_failure=coordinator.settle_terminal_append_failure,
        ),
    )
    batcher = HttpBridgeOperationEventBatcher(
        gated_coordinator,
        max_bytes=64 * 1024,
        flush_interval_seconds=60.0,
        terminal_append_timeout_seconds=0.01,
    )

    append_kwargs: dict[str, Any] = {
        "operation_id": operation_id,
        "session_id": lookup.session_id,
        "instance_id": instance_id,
        "owner_epoch": lookup.owner_epoch,
        "max_bytes": 64 * 1024,
        "state": "completed",
        "response_id": "resp-late-clear",
    }

    # Attempt 1 misses its bound and is abandoned mid-write.
    first = await batcher.append_terminal_event(event_text="first terminal\n\n", **append_kwargs)
    assert first.persisted is False
    assert first.settlement_required is True
    assert first_append_started.is_set()

    # Attempt 2 registers its own context and buffers an event for the drain.
    await batcher.enqueue(
        operation_id=operation_id,
        session_id=lookup.session_id,
        instance_id=instance_id,
        owner_epoch=lookup.owner_epoch,
        event_text="retry body\n\n",
    )

    # The abandoned attempt's cleanup runs only now, after attempt 2 took over.
    release_first_append.set()
    await _settle_detached_terminal_tasks(batcher)

    assert operation_id in await batcher.pending_operation_ids()

    await batcher.close()
