"""Regression test for the anyio 4.13 ``Lock`` lost-wakeup deadlock.

Production autopsy (2026-09-07, soju07): five HTTP-bridge ``pending_lock``
instances sat with ``_owner_task is None`` while their waiter deques held
pending, never-resolved futures. One of them queued ~100 live request tasks
behind the per-request fail-safe sweep for days.

Mechanism in anyio <= 4.13 (``anyio/_backends/_asyncio.py::Lock``):

1. owner O holds the lock, waiter W1 is queued;
2. W1 is cancelled -> its future is cancelled but W1's ``except`` branch
   (which removes W1 from the deque) has not run yet;
3. O releases in the same loop iteration: every queued future is cancelled,
   so ``release()`` sets ``_owner_task = None`` and returns;
4. newcomer A calls ``acquire()``: ``_owner_task is None`` **but** the deque
   is non-empty, so A takes the slow path and waits for a hand-off;
5. W1 finally runs and removes itself -> the deque is ``[A]`` with no owner
   and nobody left to hand ownership over. Every later acquirer queues
   behind A forever.

anyio 4.14.0 fixed this ("Fixed asyncio Lock and Semaphore deadlocks caused
by cancelled waiters left queued during release"). This test pins the
behaviour so the dependency floor cannot silently regress.
"""

from __future__ import annotations

import asyncio

import anyio
import pytest

pytestmark = pytest.mark.unit


async def _drive_release_race(primitive_factory, acquire_cm):
    lock = primitive_factory()
    hold = asyncio.Event()
    outcome: dict[str, str] = {}

    async def owner() -> None:
        async with acquire_cm(lock):
            await hold.wait()

    async def cancelled_waiter() -> None:
        async with acquire_cm(lock):
            outcome["cancelled_waiter"] = "acquired"

    async def newcomer() -> None:
        try:
            async with asyncio.timeout(1):
                async with acquire_cm(lock):
                    outcome["newcomer"] = "acquired"
        except TimeoutError:
            outcome["newcomer"] = "deadlocked"

    owner_task = asyncio.create_task(owner())
    await asyncio.sleep(0)
    waiter_task = asyncio.create_task(cancelled_waiter())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # Ready-queue order for the next iteration:
    #   owner wakes (release) -> newcomer starts (acquire) -> cancelled waiter
    #   wakes (removes its cancelled entry).
    hold.set()
    newcomer_task = asyncio.create_task(newcomer())
    waiter_task.cancel()

    await asyncio.gather(owner_task, waiter_task, newcomer_task, return_exceptions=True)
    return outcome, lock


async def test_anyio_lock_release_with_cancelled_waiter_does_not_strand_newcomer():
    outcome, lock = await _drive_release_race(anyio.Lock, lambda lock: lock)
    assert outcome.get("newcomer") == "acquired", outcome
    assert not lock.locked()
    assert lock.statistics().tasks_waiting == 0


async def test_anyio_semaphore_release_with_cancelled_waiter_does_not_strand_newcomer():
    outcome, semaphore = await _drive_release_race(lambda: anyio.Semaphore(1), lambda semaphore: semaphore)
    assert outcome.get("newcomer") == "acquired", outcome
    assert semaphore.value == 1
    assert semaphore.statistics().tasks_waiting == 0


async def test_stdlib_lock_release_with_cancelled_waiter_does_not_strand_newcomer():
    """Reference behaviour: the stdlib lock jumps stale cancelled waiters."""

    outcome, lock = await _drive_release_race(asyncio.Lock, lambda lock: lock)
    assert outcome.get("newcomer") == "acquired", outcome
    assert not lock.locked()
