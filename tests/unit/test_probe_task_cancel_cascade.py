"""Regression tests for the 2026-09-07 cancellation-cascade busy spin.

Production autopsy (soju07): 15 client-cancelled Responses requests kept three
tasks each alive for four days -- Starlette's ``StreamingResponse`` body task,
the SSE keepalive injector's ``_next_chunk`` task, and the startup-probe
``_read_first_stream_item`` task -- with ``Task.cancelling()`` at ~1.4e9.

Mechanism: a level-cancelled anyio scope re-delivers ``Task.cancel()`` to its
host task on *every* event-loop iteration until the task leaves the scope.
When that host task awaits an ``asyncio`` task directly (``await task``),
``Task.cancel()`` cascades down the ``_fut_waiter`` chain into the awaited
task, and from there into any deferring wait it is running. The deferring
helper is shielded with ``anyio.CancelScope(shield=True)``, but that shield
only protects a task anyio tracks; a plain ``asyncio.create_task`` probe is
not tracked, so the cascaded cancel re-enters its wait on every iteration and
the loop spins for as long as the deferred cleanup takes (forever, when the
cleanup is itself wedged).

The invariant under test: a level-cancelled consumer must not re-cancel the
probe task on every loop iteration, while teardown still waits for the
probe's deferred cleanup before closing the stream it drives. Awaiting the
probe through the shared-future proxy (``wait_on_shared_future`` /
``_await_task_deferring_cancellation``) breaks the cascade, so the probe sees
at most the single explicit teardown ``cancel()``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import anyio
import pytest

from app.core.utils.shared_future import _await_task_deferring_cancellation
from app.core.utils.sse import inject_sse_keepalives
from app.modules.proxy.api import _prepend_first_task

pytestmark = pytest.mark.unit


async def _run_level_cancelled_consumer(stream: AsyncIterator[str]) -> asyncio.Task[None]:
    """Consume ``stream`` inside a task group whose scope is then cancelled.

    Mirrors ``starlette.responses.StreamingResponse.__call__``: the body
    iterator runs in an anyio task group, and a client disconnect cancels the
    group's cancel scope while the body is still awaiting its first frame.
    """

    started = asyncio.Event()

    async def consume() -> None:
        started.set()
        async for _frame in stream:
            pass

    async def starlette_like_response() -> None:
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(consume)
            await started.wait()
            await asyncio.sleep(0)
            task_group.cancel_scope.cancel()

    return asyncio.create_task(starlette_like_response())


async def _empty_rest() -> AsyncIterator[str]:
    if False:  # pragma: no cover - keeps this an async generator
        yield ""


async def test_level_cancelled_response_does_not_respin_startup_probe_task():
    release = asyncio.Event()
    cleanup_task: asyncio.Task[None] | None = None

    async def probe() -> str:
        nonlocal cleanup_task

        async def cleanup() -> None:
            await release.wait()

        cleanup_task = asyncio.create_task(cleanup())
        await _await_task_deferring_cancellation(cleanup_task)
        return "data: first\n\n"

    first_task = asyncio.create_task(probe())
    await asyncio.sleep(0)
    stream = inject_sse_keepalives(_prepend_first_task(first_task, _empty_rest()), interval_seconds=60)

    response_task = await _run_level_cancelled_consumer(stream)
    # Give the old cascade ample loop iterations to manifest (it produced
    # thousands of cancels per 50ms of wall clock in the reproduction).
    await asyncio.sleep(0.05)

    assert cleanup_task is not None
    assert not first_task.done(), "deferred cleanup must keep the probe alive"
    assert first_task.cancelling() == 1, first_task.cancelling()
    assert cleanup_task.cancelling() == 0
    # Teardown still waits for the probe's deferred cleanup (ordering: the
    # probe drives the inner stream, so the body must not close it first).
    assert not response_task.done()

    release.set()
    await asyncio.wait_for(response_task, timeout=1)
    assert first_task.done()
    assert cleanup_task.done()


async def test_level_cancelled_keepalive_consumer_does_not_respin_pending_chunk_task():
    release = asyncio.Event()
    source_closed = asyncio.Event()
    cleanup_task: asyncio.Task[None] | None = None
    chunk_task: asyncio.Task[object] | None = None

    async def deferring_source() -> AsyncIterator[str]:
        nonlocal chunk_task, cleanup_task
        # ``__anext__`` runs inside the injector's ``_next_chunk`` task, so the
        # current task here is exactly the chunk task under test.
        chunk_task = asyncio.current_task()

        async def cleanup() -> None:
            await release.wait()

        cleanup_task = asyncio.create_task(cleanup())
        try:
            await _await_task_deferring_cancellation(cleanup_task)
            yield "data: first\n\n"
        finally:
            source_closed.set()

    stream = inject_sse_keepalives(deferring_source(), interval_seconds=60)
    response_task = await _run_level_cancelled_consumer(stream)
    await asyncio.sleep(0.05)

    assert chunk_task is not None, "the injector must have started its chunk task"
    assert chunk_task is not asyncio.current_task()
    assert not chunk_task.done(), "the deferring source must keep the chunk task alive"
    assert chunk_task.cancelling() == 1, chunk_task.cancelling()
    assert cleanup_task is not None
    assert cleanup_task.cancelling() == 0
    assert not response_task.done()
    assert not source_closed.is_set(), "the source must not be closed while the chunk task drives it"

    release.set()
    await asyncio.wait_for(response_task, timeout=1)
    assert chunk_task.done()
    # The chunk task settled first, then the injector's finalizer closed the
    # source exactly once (aclose on a finished generator is a no-op).
    assert source_closed.is_set()
