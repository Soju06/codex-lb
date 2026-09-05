"""Evidence that the real clock/scheduler adapters are verbatim passthroughs.

Every ``RealScheduler`` method is compared against the ``asyncio``/``anyio``
call it wraps on identical inputs. If these tests hold, injecting the default
collaborators cannot change production behavior: the seam only exists so the
virtual scheduler in ``tests/simulation`` can replace it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import timezone
from types import CoroutineType, SimpleNamespace
from typing import Any, cast

import anyio
import pytest

from app.core.clock import REAL_CLOCK, REAL_SCHEDULER, RealClock, RealScheduler, clock_for, scheduler_for

pytestmark = pytest.mark.unit


def _sleep_implementations() -> list[Any]:
    return [
        pytest.param(asyncio.sleep, id="asyncio"),
        pytest.param(RealScheduler().sleep, id="real-scheduler"),
    ]


def _wait_for_implementations() -> list[Any]:
    async def asyncio_wait_for(awaitable: Awaitable[Any], timeout: float | None) -> Any:
        return await asyncio.wait_for(awaitable, timeout=timeout)

    return [
        pytest.param(asyncio_wait_for, id="asyncio"),
        pytest.param(RealScheduler().wait_for, id="real-scheduler"),
    ]


def _wait_implementations() -> list[Any]:
    async def asyncio_wait(fs: Any, *, timeout: float | None = None, return_when: str = asyncio.ALL_COMPLETED) -> Any:
        return await asyncio.wait(fs, timeout=timeout, return_when=return_when)

    return [
        pytest.param(asyncio_wait, id="asyncio"),
        pytest.param(RealScheduler().wait, id="real-scheduler"),
    ]


def _fail_after_implementations() -> list[Any]:
    return [
        pytest.param(anyio.fail_after, id="anyio"),
        pytest.param(RealScheduler().fail_after, id="real-scheduler"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("sleep", _sleep_implementations())
async def test_sleep_zero_yields_exactly_once_and_returns_result(sleep: Callable[..., Awaitable[Any]]) -> None:
    ticks = 0
    stop = False

    async def probe() -> None:
        nonlocal ticks
        while not stop:
            ticks += 1
            await asyncio.sleep(0)

    probe_task = asyncio.create_task(probe())
    await asyncio.sleep(0)
    before = ticks

    result = await sleep(0, "yielded")

    assert result == "yielded"
    assert ticks - before == 1
    stop = True
    await probe_task


@pytest.mark.asyncio
@pytest.mark.parametrize("wait_for", _wait_for_implementations())
async def test_wait_for_zero_timeout_returns_done_future_result(wait_for: Callable[..., Awaitable[Any]]) -> None:
    future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    future.set_result("done")

    assert await wait_for(future, 0) == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("wait_for", _wait_for_implementations())
async def test_wait_for_zero_timeout_raises_for_pending_awaitable(wait_for: Callable[..., Awaitable[Any]]) -> None:
    semaphore = asyncio.Semaphore(0)

    with pytest.raises(TimeoutError):
        await wait_for(semaphore.acquire(), 0)

    assert semaphore.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("wait_for", _wait_for_implementations())
async def test_wait_for_none_timeout_awaits_result(wait_for: Callable[..., Awaitable[Any]]) -> None:
    async def value() -> str:
        await asyncio.sleep(0)
        return "value"

    assert await wait_for(value(), None) == "value"


@pytest.mark.asyncio
@pytest.mark.parametrize("wait", _wait_implementations())
async def test_wait_zero_timeout_reports_done_task(wait: Callable[..., Awaitable[Any]]) -> None:
    async def finished() -> str:
        return "finished"

    task = asyncio.create_task(finished())
    await asyncio.sleep(0)

    done, pending = await wait({task}, timeout=0)

    assert done == {task}
    assert pending == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("wait", _wait_implementations())
async def test_wait_first_completed_with_zero_timeout_matches_asyncio_shape(
    wait: Callable[..., Awaitable[Any]],
) -> None:
    async def finished() -> str:
        return "finished"

    done_task = asyncio.create_task(finished())
    never: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    await asyncio.sleep(0)

    done, pending = await wait({done_task, never}, timeout=0, return_when=asyncio.FIRST_COMPLETED)

    assert done == {done_task}
    assert pending == {never}
    assert not never.cancelled()
    never.cancel()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_after", _fail_after_implementations())
async def test_fail_after_zero_raises_timeout_error(fail_after: Callable[[float], Any]) -> None:
    with pytest.raises(TimeoutError):
        with fail_after(0):
            await asyncio.Event().wait()

    current = asyncio.current_task()
    assert current is not None
    assert current.cancelling() == 0


@pytest.mark.asyncio
async def test_real_fail_after_returns_an_anyio_cancel_scope() -> None:
    with RealScheduler().fail_after(1) as scope:
        assert isinstance(scope, anyio.CancelScope)
        await asyncio.sleep(0)
    assert scope.cancel_called is False


@pytest.mark.asyncio
async def test_create_task_returns_named_asyncio_task() -> None:
    async def value() -> str:
        return "value"

    task = RealScheduler().create_task(value(), name="parity-task")

    assert isinstance(task, asyncio.Task)
    assert task.get_name() == "parity-task"
    assert await task == "value"


def test_real_clock_tracks_stdlib_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 123.5)
    monkeypatch.setattr(time, "time", lambda: 1_700_000_000.25)

    clock = RealClock()

    assert clock.monotonic() == 123.5
    assert clock.time() == 1_700_000_000.25
    assert clock.now().tzinfo is timezone.utc


@pytest.mark.asyncio
async def test_real_scheduler_owns_nothing() -> None:
    blocked = asyncio.Event()
    unrelated = asyncio.create_task(blocked.wait())
    rerouted = REAL_SCHEDULER.create_task(blocked.wait())
    await asyncio.sleep(0)

    await REAL_SCHEDULER.cancel_owned_tasks()
    await REAL_SCHEDULER.drain()

    assert not unrelated.done()
    assert not rerouted.done()
    assert not hasattr(REAL_SCHEDULER, "_tasks")
    blocked.set()
    await asyncio.gather(unrelated, rerouted)


def test_real_scheduler_members_are_the_asyncio_primitives_themselves() -> None:
    """Aliases, not wrappers: a seam call on the relay path is the raw primitive call."""

    scheduler = RealScheduler()

    assert scheduler.wait_for is asyncio.wait_for
    assert scheduler.wait is asyncio.wait
    assert scheduler.create_task is asyncio.create_task
    assert scheduler.fail_after is anyio.fail_after


@pytest.mark.asyncio
async def test_real_scheduler_timing_methods_return_the_asyncio_coroutine_itself() -> None:
    """No wrapper frame: the per-event relay sites await the raw asyncio coroutine."""

    scheduler = RealScheduler()
    future: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def never() -> None:
        await future

    inner = never()
    coroutines: dict[Any, Any] = {
        asyncio.sleep: scheduler.sleep(0),
        asyncio.wait_for: scheduler.wait_for(inner, 1.0),
        asyncio.wait: scheduler.wait({future}, timeout=1.0),
    }
    try:
        for primitive, coroutine in coroutines.items():
            assert asyncio.iscoroutine(coroutine)
            assert cast(CoroutineType[Any, Any, Any], coroutine).cr_code is primitive.__code__, primitive.__name__
        drain = scheduler.drain()
        coroutines[asyncio.sleep] = drain
        assert cast(CoroutineType[Any, Any, Any], drain).cr_code is asyncio.sleep.__code__
    finally:
        for coroutine in coroutines.values():
            coroutine.close()
        inner.close()
        future.cancel()


def test_collaborator_accessors_default_to_real_singletons() -> None:
    bare = object()
    injected = SimpleNamespace(_clock="clock", _scheduler="scheduler")

    assert scheduler_for(bare) is REAL_SCHEDULER
    assert clock_for(bare) is REAL_CLOCK
    assert scheduler_for(injected) == "scheduler"
    assert clock_for(injected) == "clock"
