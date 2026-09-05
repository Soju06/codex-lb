"""Clock and scheduler seams for deterministic proxy lifecycle tests.

Production code reads time and schedules timers/tasks through these two
protocols so a test can substitute a virtual implementation
(``tests/simulation/virtual_time.py``) and drive a whole proxy turn without
wall-clock sleeps. The real adapters are *verbatim* passthroughs: every
``RealScheduler`` method is the corresponding ``asyncio``/``anyio`` call with
no task registry, no extra timeout and no wrapper task, so injecting the
defaults changes nothing about production behavior. The timing methods are
plain functions that return the ``asyncio`` coroutine object itself, so the
per-event relay sites (``scheduler.wait_for(queue.get(), ...)`` per delivered
block, ``scheduler.wait(...)`` per upstream message) pay no extra coroutine
frame over the raw call.

Usage rules (also enforced by ``scripts/check_proxy_timing_seams.py`` once it
lands):

* Objects that own the collaborators (``ProxyService``, ``LoadBalancer``,
  ``WorkAdmissionController``) use ``self._scheduler`` / ``self._clock``.
* Mixin methods and module functions that receive an owner read the seam with
  ``scheduler_for(owner)`` / ``clock_for(owner)``; partial doubles without the
  attribute keep the production default.
* Owner-less turn-path functions take keyword-only ``scheduler`` / ``clock``
  parameters that callers pass explicitly.
* ``asyncio.sleep(0)`` is a yield point, not a timer, and stays raw.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Coroutine, Iterable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any, Protocol, TypeVar

import anyio

T = TypeVar("T")


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def time(self) -> float: ...

    def now(self) -> datetime: ...


class Scheduler(Protocol):
    """Timing and task-spawn seam.

    The timing members are declared as plain functions returning a coroutine
    (an ``async def`` implementation satisfies them too) so the real adapter
    can hand back the ``asyncio`` coroutine object without a wrapper frame.
    """

    def sleep(self, delay: float, result: T | None = None) -> Coroutine[Any, Any, T | None]: ...

    def wait_for(self, awaitable: Awaitable[T], timeout: float | None) -> Coroutine[Any, Any, T]:
        """Bound ``awaitable`` by ``timeout``.

        Real: ``asyncio.wait_for`` verbatim. Virtual: the same inline
        ``asyncio.timeout`` shape on a virtual deadline (same task, same
        contextvars); see ``tests/simulation/test_virtual_time.py``.
        """
        ...

    def wait(
        self,
        fs: Iterable[asyncio.Future[Any]],
        *,
        timeout: float | None = None,
        return_when: str = asyncio.ALL_COMPLETED,
    ) -> Coroutine[Any, Any, tuple[set[asyncio.Future[Any]], set[asyncio.Future[Any]]]]:
        """Mirror ``asyncio.wait``: never cancels ``fs``, reports done at the deadline."""
        ...

    def create_task(self, coroutine: Coroutine[Any, Any, T], *, name: str | None = None) -> asyncio.Task[T]: ...

    def fail_after(self, delay: float) -> AbstractContextManager[Any]:
        """Mirror ``anyio.fail_after``: cancel the enclosing scope after ``delay``."""
        ...

    def drain(self) -> Coroutine[Any, Any, None]: ...

    async def cancel_owned_tasks(self) -> None: ...


class RealClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def time(self) -> float:
        return time.time()

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class RealScheduler:
    """Pure passthrough to ``asyncio``/``anyio``.

    Deliberately stateless: a registry of every rerouted task would extend task
    lifetimes and let ``cancel_owned_tasks`` on the process-wide singleton
    cancel production work. Ownership tracking exists only in the virtual
    scheduler used by tests.
    """

    # Plain functions on purpose: ``await scheduler.sleep(d)`` then awaits the
    # ``asyncio.sleep`` coroutine object itself, with no extra frame per call.
    def sleep(self, delay: float, result: T | None = None) -> Coroutine[Any, Any, T | None]:
        return asyncio.sleep(delay, result=result)

    def wait_for(self, awaitable: Awaitable[T], timeout: float | None) -> Coroutine[Any, Any, T]:
        return asyncio.wait_for(awaitable, timeout=timeout)

    def wait(
        self,
        fs: Iterable[asyncio.Future[Any]],
        *,
        timeout: float | None = None,
        return_when: str = asyncio.ALL_COMPLETED,
    ) -> Coroutine[Any, Any, tuple[set[asyncio.Future[Any]], set[asyncio.Future[Any]]]]:
        return asyncio.wait(fs, timeout=timeout, return_when=return_when)

    def create_task(self, coroutine: Coroutine[Any, Any, T], *, name: str | None = None) -> asyncio.Task[T]:
        return asyncio.create_task(coroutine, name=name)

    def fail_after(self, delay: float) -> AbstractContextManager[Any]:
        return anyio.fail_after(delay)

    def drain(self) -> Coroutine[Any, Any, None]:
        return asyncio.sleep(0)

    async def cancel_owned_tasks(self) -> None:
        # The real scheduler owns nothing; production tasks are never cancelled here.
        return None


REAL_CLOCK = RealClock()
REAL_SCHEDULER = RealScheduler()


def scheduler_for(owner: object) -> Scheduler:
    """Return the scheduler collaborator of ``owner``.

    Proxy lifecycle behavior is spread over mixins that are also reached through
    partial collaborators, so the seam reads the collaborator instead of
    requiring every holder to carry one. Anything without an explicit scheduler
    gets the real ``asyncio`` one, which keeps the production default intact.
    """

    return getattr(owner, "_scheduler", REAL_SCHEDULER)


def clock_for(owner: object) -> Clock:
    """Return the clock collaborator of ``owner``, defaulting to real time."""

    return getattr(owner, "_clock", REAL_CLOCK)
