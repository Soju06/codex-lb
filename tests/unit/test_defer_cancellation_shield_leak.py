"""Regression tests for the 2026-08-30 shield-callback event-loop livelock.

Python 3.14's ``asyncio.shield`` leaves callbacks behind on a still-pending
inner task whenever the outer await is cancelled (``_clear_awaited_by_callback``
is never detached), and every later detach pays an O(n) scan of the inner
task's callback list. The http-bridge copy of
``_await_task_deferring_cancellation`` re-shielded its task in a bare
``while True`` loop, so a level-cancelled Starlette scope (client disconnect)
busy-spun the loop against any slow cleanup task — production autopsy found
cleanup tasks carrying 100k+ leaked callbacks and the event loop starved at
~50% of GIL samples inside ``Future.remove_done_callback``.

The structural invariant under test: no matter how often a waiter is
cancelled — edge ``task.cancel()`` or a level-cancelled anyio scope — the
awaited task's callback list stays bounded, while the defer-cancellation
semantics (finish the owned task, then surface the caller's cancellation)
are preserved.
"""

from __future__ import annotations

import asyncio

import anyio
import pytest

from app.core.utils.sse import inject_sse_keepalives
from app.db.session import _shielded_bounded
from app.modules.proxy._service.http_bridge.helpers import (
    _await_task_deferring_cancellation,
)

pytestmark = pytest.mark.unit


def _callback_count(future: asyncio.Future) -> int:
    return len(getattr(future, "_callbacks", None) or [])


async def test_level_cancelled_scope_does_not_grow_task_callbacks():
    """A cancelled anyio scope must not spin-leak callbacks onto the task."""

    release = asyncio.Event()

    async def cleanup() -> str:
        await release.wait()
        return "settled"

    task = asyncio.create_task(cleanup())
    await asyncio.sleep(0)

    async def waiter() -> tuple[str, asyncio.CancelledError | None]:
        with anyio.CancelScope() as scope:
            scope.cancel()
            return await _await_task_deferring_cancellation(task)
        raise AssertionError("cancelled scope must not suppress the helper's return")

    waiter_task = asyncio.create_task(waiter())
    # Give the old busy-spin ample iterations to manifest: the unshielded
    # loop leaked >900 callbacks in 50ms of wall clock.
    await asyncio.sleep(0.05)
    assert _callback_count(task) <= 3
    assert not task.done()

    release.set()
    result, cancellation = await asyncio.wait_for(waiter_task, timeout=1)
    assert result == "settled"
    # The level cancellation blocked by the shield must still surface as the
    # deferred-cancellation marker callers re-raise after cleanup.
    assert cancellation is not None


async def test_repeated_edge_cancellation_keeps_callbacks_bounded_and_defers():
    release = asyncio.Event()

    async def cleanup() -> str:
        await release.wait()
        return "settled"

    task = asyncio.create_task(cleanup())
    waiter_task = asyncio.create_task(_await_task_deferring_cancellation(task))
    await asyncio.sleep(0)

    for _ in range(50):
        waiter_task.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert _callback_count(task) <= 3
    assert not waiter_task.done()

    release.set()
    result, cancellation = await asyncio.wait_for(waiter_task, timeout=1)
    assert result == "settled"
    assert cancellation is not None


async def test_owned_task_cancellation_still_propagates():
    async def cleanup() -> str:
        await asyncio.Event().wait()
        return "unreachable"

    task = asyncio.create_task(cleanup())
    waiter_task = asyncio.create_task(_await_task_deferring_cancellation(task))
    await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(waiter_task, timeout=1)


async def test_owned_task_exception_propagates():
    async def cleanup() -> str:
        raise RuntimeError("cleanup failed")

    task = asyncio.create_task(cleanup())
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await _await_task_deferring_cancellation(task)


async def test_sse_keepalive_ticks_do_not_grow_pending_chunk_callbacks():
    """Every keepalive timeout used to leave a shield callback on ``pending``."""

    release = asyncio.Event()

    async def quiet_then_one_chunk():
        await release.wait()
        yield "data: chunk\n\n"

    stream = inject_sse_keepalives(
        quiet_then_one_chunk(),
        interval_seconds=0.01,
        keepalive_frame=": keepalive\n\n",
        on_keepalive=None,
    )

    frames: list[str] = []

    async def consume() -> None:
        async for frame in stream:
            frames.append(frame)
            if frame == "data: chunk\n\n":
                break

    consumer = asyncio.create_task(consume())
    # Let ~20 keepalive intervals elapse against a quiet upstream.
    await asyncio.sleep(0.25)

    pending_tasks = [
        t
        for t in asyncio.all_tasks()
        if t not in {consumer, asyncio.current_task()} and "_next_chunk" in repr(t.get_coro())
    ]
    assert pending_tasks, "keepalive injector should have a pending chunk task"
    assert all(_callback_count(t) <= 3 for t in pending_tasks)

    release.set()
    await asyncio.wait_for(consumer, timeout=1)
    assert ": keepalive\n\n" in frames
    assert frames[-1] == "data: chunk\n\n"


async def test_shielded_bounded_honors_deadline_under_repeated_cancels():
    wedged = asyncio.Event()

    async def wedged_teardown() -> None:
        await wedged.wait()

    async def caller() -> asyncio.Task[object] | None:
        return await _shielded_bounded(wedged_teardown(), timeout=0.1)

    caller_task = asyncio.create_task(caller())
    await asyncio.sleep(0)
    for _ in range(10):
        caller_task.cancel()
        await asyncio.sleep(0)

    leftover = await asyncio.wait_for(caller_task, timeout=1)
    assert leftover is not None, "deadline must abandon the wedged teardown"
    assert not leftover.done()
    assert _callback_count(leftover) <= 2

    wedged.set()
    await asyncio.wait_for(leftover, timeout=1)


async def test_shielded_bounded_returns_none_when_teardown_finishes():
    async def quick_teardown() -> None:
        await asyncio.sleep(0)

    assert await _shielded_bounded(quick_teardown(), timeout=1) is None


async def test_defer_cancellation_helpers_share_the_canonical_implementation():
    """The 2026-08-30 livelock happened because one of six hand-rolled copies
    of the defer-cancellation loop missed the busy-spin guard. The alias
    imports must stay identity-equal to the canonical helpers so a new
    divergent copy cannot be reintroduced silently."""

    from app.core.utils import shared_future
    from app.modules.proxy._service.http_bridge import helpers as http_bridge_helpers
    from app.modules.proxy._service.streaming import retry as streaming_retry
    from app.modules.proxy._service.websocket import helpers as websocket_helpers

    assert http_bridge_helpers._await_task_deferring_cancellation is shared_future._await_task_deferring_cancellation
    assert streaming_retry._await_task_deferring_cancellation is shared_future._await_task_deferring_cancellation
    assert (
        websocket_helpers._await_cleanup_deferring_cancellation is shared_future._await_cleanup_deferring_cancellation
    )


async def test_api_and_forwarding_adapters_surface_the_level_cancellation_marker():
    """The bool-shaped adapters must report the shield-blocked level
    cancellation (previously suppressed until the caller's next checkpoint)."""

    from app.modules.model_sources import forwarding
    from app.modules.proxy import api as proxy_api

    async def cleanup() -> str:
        await asyncio.sleep(0)
        return "settled"

    async def run_in_cancelled_scope() -> tuple[tuple[str, bool], bool]:
        with anyio.CancelScope() as scope:
            scope.cancel()
            api_result = await proxy_api._await_result_deferring_cancellation(cleanup())
            forwarding_deferred = await forwarding._await_result_deferring_cancellation(cleanup())
            return api_result, forwarding_deferred
        raise AssertionError("cancelled scope must not suppress the adapters' return")

    task = asyncio.create_task(run_in_cancelled_scope())
    (result, api_deferred), forwarding_deferred = await asyncio.wait_for(task, timeout=1)
    assert result == "settled"
    assert api_deferred is True
    assert forwarding_deferred is True


async def test_keyed_health_drain_keeps_apply_task_callbacks_bounded():
    """The inline api_key_usage drain loop must wait leak-free: repeated
    edge cancels during a slow penalty write may not grow the owned task's
    callback list (CodeRabbit finding on #1993)."""

    from types import SimpleNamespace
    from typing import Any, cast

    from app.modules.proxy._service import api_key_usage as aku

    release = asyncio.Event()
    apply_task_holder: dict[str, asyncio.Task[None] | None] = {"task": None}

    async def slow_penalty(*_args: object) -> None:
        apply_task_holder["task"] = cast("asyncio.Task[None] | None", asyncio.current_task())
        await release.wait()

    penalty = SimpleNamespace(account=SimpleNamespace(id="acc"), error=RuntimeError("x"), code="c")
    request_state = SimpleNamespace(deferred_keyed_stream_health=[penalty])
    fake_self = SimpleNamespace(_handle_stream_error=slow_penalty)

    waiter = asyncio.create_task(
        aku._ApiKeyUsageMixin._drain_deferred_keyed_stream_health(cast(Any, fake_self), cast(Any, request_state))
    )
    while apply_task_holder["task"] is None:
        await asyncio.sleep(0)

    for _ in range(50):
        waiter.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    apply_task = apply_task_holder["task"]
    assert apply_task is not None
    assert len(getattr(apply_task, "_callbacks", None) or []) <= 3

    # The drain finishes the claimed penalty, then re-raises the deferred
    # caller cancellation.
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(waiter, timeout=1)


async def test_keyed_health_drain_surfaces_level_cancellation_after_cleanup():
    """The drain's shield blocks level cancellation; the post-shield probe
    must still re-raise it once the queue empties (uniform marker contract)."""

    from types import SimpleNamespace
    from typing import Any, cast

    from app.modules.proxy._service import api_key_usage as aku

    async def quick_penalty(*_args: object) -> None:
        await asyncio.sleep(0)

    penalty = SimpleNamespace(account=SimpleNamespace(id="acc"), error=RuntimeError("x"), code="c")
    request_state = SimpleNamespace(deferred_keyed_stream_health=[penalty])
    fake_self = SimpleNamespace(_handle_stream_error=quick_penalty)

    async def run_in_cancelled_scope() -> None:
        with anyio.CancelScope() as scope:
            scope.cancel()
            with pytest.raises(asyncio.CancelledError):
                await aku._ApiKeyUsageMixin._drain_deferred_keyed_stream_health(
                    cast(Any, fake_self), cast(Any, request_state)
                )

    await asyncio.wait_for(asyncio.create_task(run_in_cancelled_scope()), timeout=1)
    assert request_state.deferred_keyed_stream_health == []
