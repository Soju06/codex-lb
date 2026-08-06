from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Coroutine
from datetime import datetime, timezone
from typing import Any, Protocol, TypeVar

T = TypeVar("T")


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def time(self) -> float: ...

    def now(self) -> datetime: ...


class Scheduler(Protocol):
    async def sleep(self, delay: float, result: T | None = None) -> T | None: ...

    async def wait_for(self, awaitable: Awaitable[T], timeout: float | None) -> T: ...

    def create_task(self, coroutine: Coroutine[Any, Any, T], *, name: str | None = None) -> asyncio.Task[T]: ...

    async def drain(self) -> None: ...

    async def cancel_owned_tasks(self) -> None: ...


class RealClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def time(self) -> float:
        return time.time()

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class RealScheduler:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()

    async def sleep(self, delay: float, result: T | None = None) -> T | None:
        return await asyncio.sleep(delay, result=result)

    async def wait_for(self, awaitable: Awaitable[T], timeout: float | None) -> T:
        return await asyncio.wait_for(awaitable, timeout=timeout)

    def create_task(self, coroutine: Coroutine[Any, Any, T], *, name: str | None = None) -> asyncio.Task[T]:
        task = asyncio.create_task(coroutine, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self) -> None:
        if not self._tasks:
            await asyncio.sleep(0)
            return
        done = [task for task in self._tasks if task.done()]
        if done:
            await asyncio.gather(*done, return_exceptions=True)
        await asyncio.sleep(0)

    async def cancel_owned_tasks(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()


REAL_CLOCK = RealClock()
REAL_SCHEDULER = RealScheduler()
