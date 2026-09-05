"""Regression tests for the virtual scheduler's asyncio fidelity.

Each test pins one semantic the proxy harness depends on. The two Codex
findings on #1647 are covered first: ``wait_for`` with a non-positive timeout
must behave like ``asyncio.wait_for`` (raise, not sleep), and ``advance`` must
move chronologically so sequential sleeps complete under one call.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest

from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

pytestmark = pytest.mark.unit


def _scheduler() -> VirtualScheduler:
    return VirtualScheduler(VirtualClock())


@pytest.mark.asyncio
async def test_wait_for_zero_timeout_raises_like_asyncio_and_does_not_acquire() -> None:
    scheduler = _scheduler()
    semaphore = asyncio.Semaphore(1)

    with pytest.raises(asyncio.TimeoutError):
        await scheduler.wait_for(semaphore.acquire(), timeout=0)

    assert semaphore.locked() is False
    assert scheduler.owned_tasks == frozenset()


@pytest.mark.asyncio
async def test_wait_for_zero_timeout_returns_done_future_result() -> None:
    scheduler = _scheduler()
    future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    future.set_result("ready")

    assert await scheduler.wait_for(future, timeout=0) == "ready"


@pytest.mark.asyncio
async def test_wait_for_negative_timeout_behaves_like_zero() -> None:
    scheduler = _scheduler()
    semaphore = asyncio.Semaphore(0)

    with pytest.raises(asyncio.TimeoutError):
        await scheduler.wait_for(semaphore.acquire(), timeout=-1.0)

    assert semaphore.locked()
    assert scheduler.pending_timers == 0


@pytest.mark.asyncio
async def test_wait_for_positive_timeout_expires_on_advance() -> None:
    scheduler = _scheduler()
    semaphore = asyncio.Semaphore(0)
    waiter = scheduler.create_task(scheduler.wait_for(semaphore.acquire(), timeout=2.0))

    await scheduler.advance(1.999)
    assert not waiter.done()
    await scheduler.advance(0.001)

    with pytest.raises(asyncio.TimeoutError):
        await waiter
    assert scheduler.clock.monotonic() == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_two_sequential_sleeps_complete_under_one_advance() -> None:
    scheduler = _scheduler()
    wakeups: list[float] = []

    async def sleeper() -> None:
        await scheduler.sleep(0.1)
        wakeups.append(scheduler.clock.monotonic())
        await scheduler.sleep(0.1)
        wakeups.append(scheduler.clock.monotonic())

    task = scheduler.create_task(sleeper())
    await scheduler.advance(0.2)

    assert task.done()
    assert wakeups == [pytest.approx(0.1), pytest.approx(0.2)]


@pytest.mark.asyncio
async def test_three_sequential_sleeps_survive_floating_point_drift() -> None:
    scheduler = _scheduler()
    count = 0

    async def sleeper() -> None:
        nonlocal count
        for _ in range(3):
            await scheduler.sleep(0.1)
            count += 1

    task = scheduler.create_task(sleeper())
    await scheduler.advance(0.3)

    assert task.done()
    assert count == 3


@pytest.mark.asyncio
async def test_timer_armed_by_resumed_task_fires_only_when_within_target() -> None:
    scheduler = _scheduler()
    fired: list[str] = []

    async def chain(second_delay: float, label: str) -> None:
        await scheduler.sleep(0.1)
        await scheduler.sleep(second_delay)
        fired.append(label)

    within = scheduler.create_task(chain(0.05, "within"))
    beyond = scheduler.create_task(chain(0.5, "beyond"))
    await scheduler.advance(0.2)

    assert fired == ["within"]
    assert within.done()
    assert not beyond.done()
    assert scheduler.clock.monotonic() == pytest.approx(0.2)
    await scheduler.advance(0.4)
    assert fired == ["within", "beyond"]


@pytest.mark.asyncio
async def test_advance_visits_deadlines_in_chronological_order() -> None:
    scheduler = _scheduler()
    seen: list[tuple[str, float]] = []

    async def note(delay: float, label: str) -> None:
        await scheduler.sleep(delay)
        seen.append((label, scheduler.clock.monotonic()))

    scheduler.create_task(note(0.3, "late"))
    scheduler.create_task(note(0.1, "early"))
    scheduler.create_task(note(0.1, "early-too"))
    await scheduler.advance(1.0)

    assert [label for label, _ in seen] == ["early", "early-too", "late"]
    assert [at for _, at in seen] == [pytest.approx(0.1), pytest.approx(0.1), pytest.approx(0.3)]
    assert scheduler.clock.monotonic() == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_advance_rejects_negative_seconds() -> None:
    scheduler = _scheduler()

    with pytest.raises(ValueError):
        await scheduler.advance(-0.1)


@pytest.mark.asyncio
async def test_wait_zero_timeout_reports_done_task() -> None:
    scheduler = _scheduler()

    async def finished() -> str:
        return "finished"

    task = scheduler.create_task(finished())
    await scheduler.drain()

    done, pending = await scheduler.wait({task}, timeout=0)

    assert done == {task}
    assert pending == set()


@pytest.mark.asyncio
async def test_wait_first_completed_reports_deadline_tick_completion_as_done() -> None:
    scheduler = _scheduler()
    release = asyncio.Event()

    async def completes_at_deadline() -> str:
        await scheduler.sleep(1.0)
        return "at-deadline"

    async def never() -> None:
        await release.wait()

    at_deadline = scheduler.create_task(completes_at_deadline())
    pending_forever = scheduler.create_task(never())
    waiter = scheduler.create_task(
        scheduler.wait({at_deadline, pending_forever}, timeout=1.0, return_when=asyncio.FIRST_COMPLETED)
    )

    await scheduler.advance(0.999)
    assert not waiter.done()
    await scheduler.advance(0.001)

    done, pending = await waiter
    assert done == {at_deadline}
    assert pending == {pending_forever}
    assert not pending_forever.cancelled()
    release.set()
    await pending_forever


@pytest.mark.asyncio
async def test_wait_all_completed_times_out_with_partial_done_set() -> None:
    scheduler = _scheduler()
    release = asyncio.Event()

    async def quick() -> None:
        await scheduler.sleep(0.1)

    async def slow() -> None:
        await release.wait()

    quick_task = scheduler.create_task(quick())
    slow_task = scheduler.create_task(slow())
    waiter = scheduler.create_task(scheduler.wait({quick_task, slow_task}, timeout=0.5))

    await scheduler.advance(0.5)

    done, pending = await waiter
    assert done == {quick_task}
    assert pending == {slow_task}
    release.set()
    await slow_task


@pytest.mark.asyncio
async def test_wait_rejects_empty_set() -> None:
    scheduler = _scheduler()

    with pytest.raises(ValueError):
        await scheduler.wait(set(), timeout=1.0)


@pytest.mark.asyncio
async def test_fail_after_raises_timeout_error_at_deadline_and_uncancels() -> None:
    scheduler = _scheduler()
    outcome: list[str] = []

    async def body() -> None:
        try:
            with scheduler.fail_after(0.5):
                await asyncio.Event().wait()
        except TimeoutError:
            current = asyncio.current_task()
            assert current is not None
            outcome.append(f"timeout cancelling={current.cancelling()}")
            await asyncio.sleep(0)
            outcome.append("resumed")

    task = scheduler.create_task(body())
    await scheduler.advance(0.499)
    assert not task.done()
    await scheduler.advance(0.001)

    await task
    assert outcome == ["timeout cancelling=0", "resumed"]


@pytest.mark.asyncio
async def test_fail_after_exits_cleanly_and_disarms_when_body_finishes_first() -> None:
    scheduler = _scheduler()

    async def body() -> str:
        with scheduler.fail_after(5.0):
            await scheduler.sleep(0.1)
        return "finished"

    task = scheduler.create_task(body())
    await scheduler.advance(0.1)

    assert await task == "finished"
    assert scheduler.pending_timers == 0
    await scheduler.advance(10.0)
    assert task.result() == "finished"


@pytest.mark.asyncio
async def test_fail_after_expiry_racing_external_cancel_reraises_cancelled_error() -> None:
    scheduler = _scheduler()

    async def body() -> None:
        with scheduler.fail_after(0):
            await asyncio.Event().wait()

    task = asyncio.create_task(body())
    await asyncio.sleep(0)
    # Both the scope expiry (already queued by fail_after(0)) and this external
    # cancellation reach the task before it runs again.
    task.cancel()
    await asyncio.sleep(0)

    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


@pytest.mark.asyncio
async def test_fail_after_zero_cancels_at_first_checkpoint() -> None:
    scheduler = _scheduler()

    with pytest.raises(TimeoutError):
        with scheduler.fail_after(0):
            await asyncio.Event().wait()

    current = asyncio.current_task()
    assert current is not None
    assert current.cancelling() == 0


@pytest.mark.asyncio
async def test_wait_for_prefers_result_when_timeout_completes_same_tick() -> None:
    scheduler = _scheduler()
    semaphore = asyncio.Semaphore(0)

    waiter = scheduler.create_task(scheduler.wait_for(semaphore.acquire(), timeout=0.1))

    async def release_at_deadline() -> None:
        await scheduler.sleep(0.1)
        semaphore.release()

    scheduler.create_task(release_at_deadline())
    await scheduler.drain()
    await scheduler.advance(0.1)

    assert await waiter is True
    assert semaphore.locked()
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_wait_for_accepts_non_coroutine_awaitables_and_owns_created_tasks() -> None:
    scheduler = _scheduler()

    async def items() -> AsyncGenerator[str, None]:
        await scheduler.sleep(0.5)
        yield "first"

    generator = items()
    waiter = scheduler.create_task(scheduler.wait_for(generator.__anext__(), timeout=1.0))
    await scheduler.drain()
    # The waiter itself, the wrapped ``__anext__`` awaitable and the deadline timer.
    assert len(scheduler.owned_tasks) == 3

    await scheduler.advance(0.5)
    assert await waiter == "first"
    await generator.aclose()

    blocked = asyncio.Event()
    shielded_source = scheduler.create_task(blocked.wait())
    shield_waiter = scheduler.create_task(scheduler.wait_for(asyncio.shield(shielded_source), timeout=0.1))
    await scheduler.advance(0.1)
    with pytest.raises(asyncio.TimeoutError):
        await shield_waiter
    assert not shielded_source.done()
    blocked.set()
    await shielded_source


@pytest.mark.asyncio
async def test_wait_for_runs_coroutine_in_owned_child_task() -> None:
    scheduler = _scheduler()

    async def report() -> asyncio.Task[object] | None:
        return asyncio.current_task()

    virtual_inner = await scheduler.wait_for(report(), timeout=1.0)
    real_inner = await asyncio.wait_for(report(), timeout=1.0)

    # Documented divergence: real asyncio (3.12+) awaits the coroutine inline.
    assert real_inner is asyncio.current_task()
    assert virtual_inner is not asyncio.current_task()


@pytest.mark.asyncio
async def test_cancel_owned_tasks_clears_tasks_and_timers() -> None:
    scheduler = _scheduler()
    blocked = asyncio.Event()
    owned = scheduler.create_task(blocked.wait())
    sleeper = scheduler.create_task(scheduler.sleep(10.0))
    await scheduler.drain()
    assert scheduler.pending_timers == 1
    assert scheduler.owned_tasks == {owned, sleeper}

    await scheduler.cancel_owned_tasks()

    assert owned.cancelled()
    assert sleeper.cancelled()
    assert scheduler.owned_tasks == frozenset()
    assert scheduler.pending_timers == 0


@pytest.mark.asyncio
async def test_cancelled_sleep_disarms_its_timer() -> None:
    scheduler = _scheduler()
    sleeper = scheduler.create_task(scheduler.sleep(1.0))
    await scheduler.drain()
    assert scheduler.pending_timers == 1

    sleeper.cancel()
    await asyncio.gather(sleeper, return_exceptions=True)

    assert scheduler.pending_timers == 0


@pytest.mark.asyncio
async def test_drain_rounds_is_overridable() -> None:
    class ShallowScheduler(VirtualScheduler):
        drain_rounds = 1

    scheduler = ShallowScheduler(VirtualClock())
    hops = 0

    async def hopper() -> None:
        nonlocal hops
        for _ in range(4):
            await asyncio.sleep(0)
            hops += 1

    task = scheduler.create_task(hopper())
    await scheduler.drain()

    assert not task.done()
    assert hops < 4
    await task
