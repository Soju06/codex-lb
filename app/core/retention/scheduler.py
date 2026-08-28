from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol, TypeVar, cast

from app.core.retention.job import get_effective_retention, run_retention_pass

logger = logging.getLogger(__name__)

RETENTION_INTERVAL_SECONDS = 3600


_T = TypeVar("_T")


class _LeaderElectionLike(Protocol):
    async def run_if_leader(self, fn: Callable[[], Awaitable[_T]]) -> _T | None: ...


def _get_leader_election() -> _LeaderElectionLike:
    module = importlib.import_module("app.core.scheduling.leader_election")
    return cast(_LeaderElectionLike, module.get_leader_election())


@dataclass(slots=True)
class DataRetentionScheduler:
    """Always-on hourly tick that re-resolves the effective retention.

    Retention is a runtime (dashboard) setting, so enablement cannot be
    frozen at startup: each tick reads the SettingsCache-backed effective
    configuration and skips (before leader election) while retention is
    disabled. The pass itself stays gated behind the heartbeat-renewed
    ``run_if_leader`` so at most one instance prunes at a time.
    """

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
            await self._prune_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _prune_once(self) -> None:
        try:
            retention = await get_effective_retention()
        except Exception:
            logger.exception("Failed to resolve effective data retention settings")
            return
        if not retention.enabled:
            return
        await _get_leader_election().run_if_leader(self._prune_as_leader)

    async def _prune_as_leader(self) -> None:
        async with self._lock:
            try:
                await run_retention_pass()
            except Exception:
                logger.exception("Data retention pass failed")


def build_data_retention_scheduler() -> DataRetentionScheduler:
    return DataRetentionScheduler(interval_seconds=RETENTION_INTERVAL_SECONDS)
