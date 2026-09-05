from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class VirtualClock:
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


@dataclass(order=True, slots=True)
class _Timer:
    deadline: float
    sequence: int
    future: asyncio.Future[Any] = field(compare=False)
    result: Any = field(compare=False)


class VirtualScheduler:
    def __init__(self, clock: VirtualClock) -> None:
        self.clock = clock
        self._timers: list[_Timer] = []
        self._tasks: set[asyncio.Task[Any]] = set()
        self._sequence = 0

    async def sleep(self, delay: float, result: T | None = None) -> T | None:
        if delay <= 0:
            await asyncio.sleep(0)
            return result
        loop = asyncio.get_running_loop()
        future: asyncio.Future[T | None] = loop.create_future()
        self._sequence += 1
        self._timers.append(_Timer(self.clock.monotonic() + delay, self._sequence, future, result))
        self._timers.sort()
        return await future

    async def wait_for(self, awaitable: Awaitable[T], timeout: float | None) -> T:
        task = asyncio.ensure_future(awaitable)
        if timeout is None:
            return await task
        timeout_task = self.create_task(self.sleep(timeout))
        try:
            done, pending = await asyncio.wait({task, timeout_task}, return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                timeout_task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                return task.result()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise asyncio.TimeoutError
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            timeout_task.cancel()
            await asyncio.gather(timeout_task, return_exceptions=True)
            raise

    def create_task(self, coroutine: Coroutine[Any, Any, T], *, name: str | None = None) -> asyncio.Task[T]:
        task = asyncio.create_task(coroutine, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def advance(self, seconds: float) -> None:
        self.clock.advance(seconds)
        due = [timer for timer in self._timers if timer.deadline <= self.clock.monotonic()]
        self._timers = [timer for timer in self._timers if timer.deadline > self.clock.monotonic()]
        for timer in due:
            if not timer.future.done():
                timer.future.set_result(timer.result)
        await self.drain()

    async def drain(self) -> None:
        for _ in range(8):
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
