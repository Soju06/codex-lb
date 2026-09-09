from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field

from app.core.retention.job import run_retention_pass
from app.core.scheduling.leader_election_handle import get_leader_election as _get_leader_election

logger = logging.getLogger(__name__)

RETENTION_INTERVAL_SECONDS = 3600


@dataclass(slots=True)
class DataRetentionScheduler:
    """Always-on hourly tick running the leader-gated retention pass.

    Retention is a runtime (dashboard) setting, so enablement cannot be
    frozen at startup: the pass re-resolves the SettingsCache-backed effective
    windows on every tick. The tick never short-circuits on disabled windows
    because the model-source pin purge inside the pass is not opt-in (pins
    carry their own ``purge_at``); the pass stays gated behind the
    heartbeat-renewed ``run_if_leader`` so at most one instance prunes at a
    time.
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
        await _get_leader_election().run_if_leader(self._prune_as_leader)

    async def _prune_as_leader(self) -> None:
        async with self._lock:
            try:
                await run_retention_pass()
            except Exception:
                logger.exception("Data retention pass failed")


def build_data_retention_scheduler() -> DataRetentionScheduler:
    return DataRetentionScheduler(interval_seconds=RETENTION_INTERVAL_SECONDS)
