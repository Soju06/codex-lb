"""Known dependency defect surfaced by the bridge-turn property: the anyio 4.13 asyncio ``Lock`` wedge.

``anyio._backends._asyncio.Lock.release`` (4.13.0, the ``uv.lock`` pin) skips
waiters whose future is already cancelled but leaves their entries queued.
When a waiter parked on the lock is raw-cancelled (``asyncio.Task.cancel()``,
the shape ``_await_cancelled_task`` gives the bridge reader while it sits on
``session.pending_lock``) in the same loop tick as the holder's ``release()``,
the lock ends up unowned with a stale entry, an acquirer that was already
runnable sees a non-empty waiter queue, takes the slow path and parks, and the
cancelled waiter's cleanup finds no owner to hand over from. Every later
``acquire()`` parks forever. anyio 4.14.0 fixed it ("Fixed asyncio ``Lock`` and
``Semaphore`` deadlocks caused by cancelled waiters left queued during
release", agronholm/anyio#1145).

This is the minimal shape, without the harness; the seeds of the production
turn that wedge ``pending_lock`` the same way are pinned in
``test_proxy_turn_lifecycle_property.py``. Both are strict expected failures on
the pinned anyio so a widened schedule count or a dependency bump reports the
change instead of looking like harness rot.
"""

from __future__ import annotations

import asyncio
from importlib.metadata import version

import anyio
import pytest

pytestmark = pytest.mark.unit


def _anyio_version() -> tuple[int, int]:
    major, minor = version("anyio").split(".")[:2]
    return int(major), int(minor)


ANYIO_LOCK_RELEASE_SKIPS_CANCELLED_WAITERS = _anyio_version() < (4, 14)
ANYIO_LOCK_WEDGE_REASON = (
    f"anyio {version('anyio')} < 4.14.0: Lock.release() leaves a cancelled waiter queued, so the lock is left "
    "unowned with a stale entry and the next acquirer parks forever (fixed upstream in 4.14.0, anyio#1145). "
    "Strict: an anyio bump must turn this into a pass."
)


@pytest.mark.asyncio
@pytest.mark.xfail(
    ANYIO_LOCK_RELEASE_SKIPS_CANCELLED_WAITERS,
    strict=True,
    raises=AssertionError,
    reason=ANYIO_LOCK_WEDGE_REASON,
)
async def test_lock_release_racing_a_cancelled_waiter_hands_over_to_the_next_acquirer() -> None:
    lock = anyio.Lock()
    await lock.acquire()  # this task is the holder

    async def parked_waiter() -> None:
        async with lock:
            pass

    parked = asyncio.create_task(parked_waiter())
    await asyncio.sleep(0)  # parked on the lock

    go = asyncio.Event()

    async def late_acquirer() -> str:
        await go.wait()
        async with lock:
            return "acquired"

    late = asyncio.create_task(late_acquirer())
    await asyncio.sleep(0)  # parked on the event

    go.set()  # the acquirer is runnable before ...
    parked.cancel()  # ... the parked waiter is raw-cancelled ...
    lock.release()  # ... and the holder releases in the same tick
    for _ in range(10):
        await asyncio.sleep(0)
    await asyncio.gather(parked, return_exceptions=True)

    wedged = not late.done()
    statistics = lock.statistics()
    if wedged:
        late.cancel()
        await asyncio.gather(late, return_exceptions=True)
    assert not wedged, f"lock wedged: locked={statistics.locked} tasks_waiting={statistics.tasks_waiting}"
    assert late.result() == "acquired"
