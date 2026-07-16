from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol, TypeVar, cast

from app.modules.accounts.usage_rollup import run_fold_pass

logger = logging.getLogger(__name__)

FOLD_INTERVAL_SECONDS = 900


_T = TypeVar("_T")


class _LeaderElectionLike(Protocol):
    async def run_if_leader(self, fn: Callable[[], Awaitable[_T]]) -> _T | None: ...


def _get_leader_election() -> _LeaderElectionLike:
    module = importlib.import_module("app.core.scheduling.leader_election")
    return cast(_LeaderElectionLike, module.get_leader_election())


@dataclass(slots=True)
class AccountUsageRollupScheduler:
    interval_seconds: int
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            await self._fold_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _fold_once(self) -> None:
        await _get_leader_election().run_if_leader(self._fold_as_leader)

    async def _fold_as_leader(self) -> None:
        async with self._lock:
            try:
                await run_fold_pass()
            except Exception:
                logger.exception("Account usage rollup fold pass failed")


def build_account_usage_rollup_scheduler() -> AccountUsageRollupScheduler:
    return AccountUsageRollupScheduler(interval_seconds=FOLD_INTERVAL_SECONDS)
