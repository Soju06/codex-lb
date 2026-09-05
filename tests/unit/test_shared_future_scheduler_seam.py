"""The deferring-cancellation helpers spawn coroutines through the scheduler seam.

``_await_result_deferring_cancellation`` used to ``asyncio.ensure_future`` its
awaitable, an unowned spawn channel a simulation could not see even though
every bridge cleanup site (``_release_websocket_response_create_gate`` and the
route-level cleanups in ``api.py``) reaches it. Under the real default the
behaviour is unchanged (``asyncio.create_task``); under a ``VirtualScheduler``
the task is owned, so quiescence proofs cover it.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from app.core.clock import REAL_SCHEDULER
from app.core.utils.shared_future import (
    _await_cleanup_deferring_cancellation,
    _await_result_deferring_cancellation,
)
from app.modules.proxy._service.websocket.helpers import _release_websocket_response_create_gate
from app.modules.proxy.service import _WebSocketRequestState
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_coroutine_is_spawned_through_the_scheduler_and_owned() -> None:
    scheduler = VirtualScheduler(VirtualClock())
    seen: list[frozenset[asyncio.Task[object]]] = []

    async def cleanup() -> str:
        seen.append(scheduler.owned_tasks)
        await asyncio.sleep(0)
        return "cleaned"

    result, cancellation = await _await_result_deferring_cancellation(cleanup(), scheduler=scheduler)

    assert result == "cleaned"
    assert cancellation is None
    assert len(seen) == 1 and len(seen[0]) == 1, "the cleanup task ran as an owned scheduler task"
    assert scheduler.owned_tasks == frozenset()


@pytest.mark.asyncio
async def test_non_coroutine_awaitable_still_goes_through_ensure_future() -> None:
    scheduler = VirtualScheduler(VirtualClock())
    future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    future.set_result("ready")

    result, cancellation = await _await_result_deferring_cancellation(future, scheduler=scheduler)

    assert result == "ready"
    assert cancellation is None
    assert scheduler.owned_tasks == frozenset()


@pytest.mark.asyncio
async def test_real_default_spawns_a_plain_asyncio_task() -> None:
    async def cleanup() -> asyncio.Task[object] | None:
        return asyncio.current_task()

    task, cancellation = await _await_result_deferring_cancellation(cleanup(), scheduler=REAL_SCHEDULER)

    assert isinstance(task, asyncio.Task)
    assert task is not asyncio.current_task()
    assert cancellation is None
    assert await _await_cleanup_deferring_cancellation(asyncio.sleep(0)) is None


@pytest.mark.asyncio
async def test_release_gate_owns_the_deferred_account_lease_release() -> None:
    scheduler = VirtualScheduler(VirtualClock())
    gate = asyncio.Semaphore(0)
    released: list[object] = []
    release_started = asyncio.Event()

    async def release_lease(lease: object) -> None:
        release_started.set()
        released.append(lease)
        await scheduler.sleep(0.5)

    request_state = _WebSocketRequestState(
        request_id="req-seam",
        model="gpt-5.5",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        transport="http",
        response_create_gate=gate,
        response_create_gate_acquired=True,
        awaiting_response_created=True,
    )
    request_state.account_response_create_lease = cast(Any, "lease")
    request_state.account_response_create_release = release_lease

    releaser = scheduler.create_task(
        _release_websocket_response_create_gate(request_state, gate, scheduler=scheduler),
    )
    await scheduler.drain()

    assert release_started.is_set()
    assert len(scheduler.owned_tasks) == 2, "the releaser and the owned lease-release task"
    assert not releaser.done()

    await scheduler.advance(0.5)

    assert releaser.done() and releaser.exception() is None
    assert released == ["lease"]
    assert gate._value == 1
    assert scheduler.owned_tasks == frozenset()
