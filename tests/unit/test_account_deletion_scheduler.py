"""Shutdown promptness of the account-deletion worker loop (issue #2338).

The loop's idle wait is ``DELETION_INTERVAL_SECONDS`` long, so ``stop()`` has
to end it on the same event-loop turn. Cancellation alone does not guarantee
that: the tick's own session teardown swallows it. When the tick's ``SELECT``
fails, ``get_background_session`` runs ``_safe_rollback`` on the loop's frame,
and that helper discards a ``CancelledError`` landing inside it — dropped
outright by the bounded SQLite teardown wait, re-raised into
``except BaseException: return`` on the unbounded one. A swallowed cancel used
to leave the loop parked for a whole interval — longer than the shutdown drain
budget and than the owned launcher's lifespan-cleanup bound.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from app.modules.accounts.deletion import DELETION_INTERVAL_SECONDS, AccountDeletionScheduler

pytestmark = pytest.mark.unit

# Generous enough to be immune to scheduling noise, and orders of magnitude
# below the interval the regression parked on.
_STOP_BOUND_SECONDS = 1.0


async def _stop_within_bound(scheduler: AccountDeletionScheduler) -> float | None:
    """Return ``None`` if ``stop()`` finished inside the bound, else the bound.

    ``asyncio.wait`` rather than ``asyncio.wait_for``: ``stop()`` awaits its
    loop task under ``contextlib.suppress(asyncio.CancelledError)``, so a
    ``wait_for`` timeout would be swallowed by ``stop()`` itself and reported
    as a successful (but interval-long) return.
    """
    loop_task = scheduler._task
    assert loop_task is not None
    stop_task = asyncio.create_task(scheduler.stop())
    _done, pending = await asyncio.wait({stop_task}, timeout=_STOP_BOUND_SECONDS)
    if not pending:
        assert loop_task.done()
        return None
    stop_task.cancel()
    loop_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await loop_task
    with contextlib.suppress(asyncio.CancelledError):
        await stop_task
    return _STOP_BOUND_SECONDS


@pytest.mark.asyncio
async def test_stop_ends_the_interval_wait_when_the_tick_absorbs_the_cancellation(monkeypatch) -> None:
    """A tick that swallows the stop cancellation must not park shutdown.

    Before the fix the loop fell straight through the absorbed cancel into
    ``wait_for(self._wake.wait(), timeout=self.interval_seconds)`` and only
    re-read ``_stop`` when that timer expired ``DELETION_INTERVAL_SECONDS``
    later, which is exactly the stall captured in issue #2338.
    """
    entered = asyncio.Event()

    async def _absorbing_tick(self: AccountDeletionScheduler) -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return

    monkeypatch.setattr(AccountDeletionScheduler, "_run_once", _absorbing_tick)
    scheduler = AccountDeletionScheduler(interval_seconds=DELETION_INTERVAL_SECONDS)
    await scheduler.start()
    await entered.wait()

    overrun = await _stop_within_bound(scheduler)

    assert overrun is None, f"stop() was still blocked {overrun}s after an absorbed cancellation"


@pytest.mark.asyncio
async def test_stop_ends_the_idle_interval_wait_without_running_another_pass(monkeypatch) -> None:
    """Releasing the wait must not be mistaken for a wake-up nudge."""
    passes = 0
    ticked = asyncio.Event()

    async def _counting_tick(self: AccountDeletionScheduler) -> None:
        nonlocal passes
        passes += 1
        ticked.set()

    monkeypatch.setattr(AccountDeletionScheduler, "_run_once", _counting_tick)
    scheduler = AccountDeletionScheduler(interval_seconds=DELETION_INTERVAL_SECONDS)
    await scheduler.start()
    await ticked.wait()
    await asyncio.sleep(0)  # let the loop reach its interval wait

    overrun = await _stop_within_bound(scheduler)

    assert overrun is None, f"stop() was still blocked {overrun}s from the idle interval wait"
    assert passes == 1


@pytest.mark.asyncio
async def test_wake_still_starts_the_next_pass_before_the_interval(monkeypatch) -> None:
    """Mutant guard: the wake signal must keep driving the fast delete path."""
    ticked = asyncio.Event()
    passes = 0

    async def _counting_tick(self: AccountDeletionScheduler) -> None:
        nonlocal passes
        passes += 1
        ticked.set()

    monkeypatch.setattr(AccountDeletionScheduler, "_run_once", _counting_tick)
    scheduler = AccountDeletionScheduler(interval_seconds=DELETION_INTERVAL_SECONDS)
    await scheduler.start()
    await ticked.wait()
    ticked.clear()

    scheduler.wake()
    await asyncio.wait_for(ticked.wait(), timeout=_STOP_BOUND_SECONDS)

    assert passes == 2
    assert await _stop_within_bound(scheduler) is None
