"""Virtual clock and scheduler for deterministic proxy lifecycle tests.

``VirtualScheduler`` implements ``app.core.clock.Scheduler`` on top of the
running pytest event loop. Tasks still run on the real loop; only *time* is
virtual: every ``sleep``/``wait_for``/``wait``/``fail_after`` deadline becomes a
timer that fires when a test calls ``advance``. The scheduler also owns every
task it creates so a test can prove quiescence (``all(task.done())``) and can
tear a turn down with ``cancel_owned_tasks``.

Fidelity notes (each pinned by ``tests/simulation/test_virtual_time.py``):

* ``sleep``/``wait_for``/``wait`` follow CPython ``asyncio`` semantics for
  ``timeout <= 0`` (``wait_for`` raises ``TimeoutError`` unless the awaitable is
  already done; ``wait`` reports whatever is done after one loop turn).
* ``advance`` moves the clock chronologically: it steps to the earliest due
  deadline, fires the timers due there, drains, and repeats until nothing is
  due at or before the target. Timers armed by resumed tasks fire within the
  same ``advance`` when their deadline is still at or before the target.
* ``wait_for`` has the shape of ``asyncio.wait_for`` on 3.12+: the awaitable is
  awaited *inline* in the calling task under an ``asyncio.timeout``-style
  deadline (``_VirtualTimeout`` mirrors ``asyncio.timeouts.Timeout`` on a
  virtual timer). ``current_task()`` inside the awaited coroutine is the
  caller, contextvar writes are visible to the caller, task-bound primitives
  such as ``anyio.Lock`` keep working, a plain awaited future is cancelled on
  expiry, and the deadline cuts through an ``anyio.CancelScope(shield=True)``
  exactly as the real primitive does. A same-tick tie between the awaitable
  and its deadline reports ``TimeoutError``, as CPython does when its timer
  callback runs before the task's wakeup.
* ``fail_after`` enters a real ``anyio.CancelScope`` and cancels *that scope*
  when the virtual deadline fires, so anyio's own machinery delivers the
  cancellation: an inner ``anyio.CancelScope(shield=True)`` finishes first,
  the cancellation is re-delivered at every checkpoint while the scope is
  active, and an external cancellation racing the expiry is kept. Only the
  clock that decides *when* the scope is cancelled is virtual.
* ``drain`` runs a fixed number of loop turns (``drain_rounds``). Quiescence is
  asserted by callers, so too small a round count fails loudly rather than
  silently reordering events. Each round also samples the done-callback count
  of every pending owned task (``max_pending_owned_task_callbacks``): a wait
  loop that re-shields a still-pending task grows that count per cancelled
  attempt (the 2026-08-30 event-loop livelock), while the fan-out helper keeps
  it constant.
"""

from __future__ import annotations

import asyncio
import heapq
from collections.abc import Awaitable, Coroutine, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import TracebackType
from typing import Any, TypeVar

import anyio

T = TypeVar("T")

# Mirrors asyncio's ``clock_resolution`` slack: a timer whose deadline is within
# this distance of the target is due. Sequential ``sleep(0.1)`` calls otherwise
# accumulate binary floating-point error past an ``advance(0.3)`` target.
_DEADLINE_SLACK_SECONDS = 1e-9


@dataclass(slots=True)
class VirtualClock:
    """Manually advanced clock.

    Never call ``VirtualClock.advance`` directly while a ``VirtualScheduler``
    owns timers: the scheduler would not see the new time and its due timers
    would not fire. Use ``VirtualScheduler.advance`` instead.
    """

    monotonic_value: float = 0.0
    epoch_value: float = 1_700_000_000.0

    def monotonic(self) -> float:
        return self.monotonic_value

    def time(self) -> float:
        return self.epoch_value

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.epoch_value, tz=timezone.utc)

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("virtual time cannot move backwards")
        self.monotonic_value += seconds
        self.epoch_value += seconds

    def step_to(self, monotonic: float) -> None:
        """Move to an exact monotonic instant (no floating-point drift)."""

        if monotonic < self.monotonic_value:
            raise ValueError("virtual time cannot move backwards")
        self.epoch_value += monotonic - self.monotonic_value
        self.monotonic_value = monotonic


@dataclass(order=True, slots=True)
class _Timer:
    # Ties fire in registration order: ``sequence`` is the deterministic tie-break.
    deadline: float
    sequence: int
    future: asyncio.Future[Any] = field(compare=False)
    result: Any = field(compare=False)


def _pending_callback_count(future: asyncio.Future[Any]) -> int:
    callbacks = getattr(future, "_callbacks", None)
    return len(callbacks) if callbacks is not None else 0


class _VirtualTimeout:
    """``asyncio.timeouts.Timeout`` on a virtual deadline.

    Same shape as the CPython class ``asyncio.wait_for`` uses on 3.12+: the
    deadline cancels the *entering task* once (an edge cancellation that any
    awaited future or shielded scope in the body sees), and ``__exit__``
    converts that cancellation into ``TimeoutError`` only when no other
    cancellation request arrived in the meantime (``uncancel`` bookkeeping).
    """

    def __init__(self, scheduler: VirtualScheduler, delay: float) -> None:
        self._scheduler = scheduler
        self._delay = delay
        self._task: asyncio.Task[Any] | None = None
        self._timer: _Timer | None = None
        self._cancelling = 0
        self._expired = False
        self._exited = False

    def __enter__(self) -> _VirtualTimeout:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("wait_for() must be awaited from a running task")
        self._task = task
        self._cancelling = task.cancelling()
        if self._delay <= 0:
            asyncio.get_running_loop().call_soon(self._on_timeout)
        else:
            self._timer = self._scheduler._arm(self._delay)
            self._timer.future.add_done_callback(self._on_timer)
        return self

    def _on_timer(self, future: asyncio.Future[Any]) -> None:
        if not future.cancelled():
            self._on_timeout()

    def _on_timeout(self) -> None:
        # ``_exited`` guards the tick where the awaitable completed and the
        # body left the scope before this callback ran; CPython avoids the
        # same stray cancellation by cancelling its timer handle on exit.
        if self._exited or self._task is None or self._task.done():
            return
        self._expired = True
        self._task.cancel()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        # Never suppresses: it either re-raises the deadline as ``TimeoutError``
        # or lets the body's exception propagate.
        self._exited = True
        if self._timer is not None:
            self._scheduler._disarm(self._timer)
        if self._expired:
            assert self._task is not None
            if (
                self._task.uncancel() <= self._cancelling
                and exc_type is not None
                and issubclass(exc_type, asyncio.CancelledError)
            ):
                raise TimeoutError from exc


class _VirtualFailAfter:
    """Synchronous context manager returned by ``VirtualScheduler.fail_after``.

    ``anyio.fail_after`` is ``with CancelScope(deadline=...) as scope: yield``
    followed by ``raise TimeoutError`` when the scope caught its own
    cancellation at the deadline. This twin keeps that structure and only
    replaces the deadline source: a virtual timer calls ``scope.cancel()``, so
    shielding, per-checkpoint re-delivery, ``uncancel`` bookkeeping and the
    treatment of a racing external cancellation are anyio's own.
    """

    def __init__(self, scheduler: VirtualScheduler, delay: float) -> None:
        self._scheduler = scheduler
        self._delay = delay
        self._scope: anyio.CancelScope | None = None
        self._timer: _Timer | None = None
        self._expired = False
        self._exited = False

    def __enter__(self) -> anyio.CancelScope:
        if asyncio.current_task() is None:
            raise RuntimeError("fail_after() must be entered from a running task")
        scope = anyio.CancelScope()
        scope.__enter__()
        self._scope = scope
        if self._delay <= 0:
            # anyio cancels a scope whose deadline already passed on entry; the
            # host task then sees the cancellation at its first checkpoint.
            self._expired = True
            scope.cancel()
        else:
            self._timer = self._scheduler._arm(self._delay)
            self._timer.future.add_done_callback(self._on_timer)
        return scope

    def _on_timer(self, future: asyncio.Future[Any]) -> None:
        if future.cancelled() or self._exited or self._scope is None:
            return
        self._expired = True
        self._scope.cancel()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self._exited = True
        if self._timer is not None:
            self._scheduler._disarm(self._timer)
        assert self._scope is not None
        swallowed = self._scope.__exit__(exc_type, exc, traceback)
        if self._expired and self._scope.cancelled_caught:
            raise TimeoutError
        return bool(swallowed)


class VirtualScheduler:
    """``Scheduler`` whose timers fire only when ``advance`` is called."""

    drain_rounds = 32

    def __init__(self, clock: VirtualClock) -> None:
        self.clock = clock
        self._timers: list[_Timer] = []
        self._tasks: set[asyncio.Task[Any]] = set()
        self._sequence = 0
        self.max_pending_owned_task_callbacks = 0

    # -- introspection -------------------------------------------------------

    @property
    def owned_tasks(self) -> frozenset[asyncio.Task[Any]]:
        return frozenset(self._tasks)

    @property
    def pending_timers(self) -> int:
        return sum(1 for timer in self._timers if not timer.future.done())

    def sample_owned_task_callbacks(self) -> int:
        """Record and return the largest done-callback count on a pending owned task."""

        count = max((_pending_callback_count(task) for task in self._tasks if not task.done()), default=0)
        self.max_pending_owned_task_callbacks = max(self.max_pending_owned_task_callbacks, count)
        return count

    # -- timers ---------------------------------------------------------------

    def _arm(self, delay: float, result: Any = None) -> _Timer:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._sequence += 1
        timer = _Timer(self.clock.monotonic() + delay, self._sequence, future, result)
        heapq.heappush(self._timers, timer)
        return timer

    def _disarm(self, timer: _Timer) -> None:
        if not timer.future.done():
            timer.future.cancel()
        if timer in self._timers:
            self._timers.remove(timer)
            heapq.heapify(self._timers)

    def _own(self, task: asyncio.Task[Any]) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # -- Scheduler protocol ---------------------------------------------------

    async def sleep(self, delay: float, result: T | None = None) -> T | None:
        if delay <= 0:
            await asyncio.sleep(0)
            return result
        timer = self._arm(delay, result)
        try:
            return await timer.future
        finally:
            self._disarm(timer)

    async def wait_for(self, awaitable: Awaitable[T], timeout: float | None) -> T:
        if timeout is None:
            return await awaitable
        if timeout <= 0:
            # CPython special case: schedule, then cancel without ever running
            # a not-yet-started coroutine. A task created here is owned.
            future: asyncio.Future[T]
            if isinstance(awaitable, asyncio.Future):
                future = awaitable
            else:
                future = asyncio.ensure_future(awaitable)
                if isinstance(future, asyncio.Task):
                    self._own(future)
            if future.done():
                return future.result()
            future.cancel()
            await asyncio.gather(future, return_exceptions=True)
            try:
                return future.result()
            except asyncio.CancelledError as exc:
                raise TimeoutError from exc
        with _VirtualTimeout(self, timeout):
            return await awaitable

    @staticmethod
    def _wait_satisfied(done: set[asyncio.Future[Any]], fs: set[asyncio.Future[Any]], return_when: str) -> bool:
        if return_when == asyncio.FIRST_COMPLETED:
            return bool(done)
        if return_when == asyncio.ALL_COMPLETED:
            return done == fs
        return done == fs or any(not f.cancelled() and f.exception() is not None for f in done)

    async def wait(
        self,
        fs: Iterable[asyncio.Future[Any]],
        *,
        timeout: float | None = None,
        return_when: str = asyncio.ALL_COMPLETED,
    ) -> tuple[set[asyncio.Future[Any]], set[asyncio.Future[Any]]]:
        futures = set(fs)
        if not futures:
            raise ValueError("Set of Tasks/Futures is empty.")
        if return_when not in (asyncio.FIRST_COMPLETED, asyncio.FIRST_EXCEPTION, asyncio.ALL_COMPLETED):
            raise ValueError(f"Invalid return_when value: {return_when}")
        if timeout is None:
            return await asyncio.wait(futures, return_when=return_when)
        timer = self.create_task(self.sleep(timeout))
        try:
            # ``asyncio.wait`` always suspends at least once, even when every
            # future is already done.
            await asyncio.sleep(0)
            while True:
                done = {future for future in futures if future.done()}
                if self._wait_satisfied(done, futures, return_when) or timer.done():
                    # Recomputed after the timer fired, so a future completing
                    # in the deadline tick is reported done, as with asyncio.
                    return done, futures - done
                await asyncio.wait((futures - done) | {timer}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            if not timer.done():
                timer.cancel()
                await asyncio.gather(timer, return_exceptions=True)

    def create_task(self, coroutine: Coroutine[Any, Any, T], *, name: str | None = None) -> asyncio.Task[T]:
        task = asyncio.create_task(coroutine, name=name)
        self._own(task)
        return task

    def fail_after(self, delay: float) -> _VirtualFailAfter:
        return _VirtualFailAfter(self, delay)

    async def drain(self) -> None:
        for _ in range(self.drain_rounds):
            self.sample_owned_task_callbacks()
            await asyncio.sleep(0)

    async def cancel_owned_tasks(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        for timer in self._timers:
            if not timer.future.done():
                timer.future.cancel()
        self._timers.clear()

    # -- virtual time ---------------------------------------------------------

    def _pop_due_timers(self, now: float) -> list[_Timer]:
        due: list[_Timer] = []
        while self._timers and self._timers[0].deadline <= now + _DEADLINE_SLACK_SECONDS:
            timer = heapq.heappop(self._timers)
            if not timer.future.done():
                due.append(timer)
        return due

    def _next_deadline(self) -> float | None:
        while self._timers and self._timers[0].future.done():
            heapq.heappop(self._timers)
        return self._timers[0].deadline if self._timers else None

    async def advance(self, seconds: float) -> None:
        """Move virtual time forward by ``seconds``, firing every timer due on the way.

        Timers fire in ``(deadline, sequence)`` order with the clock set to each
        deadline in turn, and the loop is drained after each batch so tasks that
        arm new timers at or before the target also fire within this call.
        """

        if seconds < 0:
            raise ValueError("virtual time cannot move backwards")
        target = self.clock.monotonic() + seconds
        await self.drain()
        while True:
            next_deadline = self._next_deadline()
            if next_deadline is None or next_deadline > target + _DEADLINE_SLACK_SECONDS:
                break
            if next_deadline > self.clock.monotonic():
                self.clock.step_to(next_deadline)
            for timer in self._pop_due_timers(self.clock.monotonic()):
                timer.future.set_result(timer.result)
            await self.drain()
        if self.clock.monotonic() < target:
            self.clock.step_to(target)
            await self.drain()
