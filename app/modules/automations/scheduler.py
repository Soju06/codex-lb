from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING

from app.core.config.background_jobs import resolve_background_job_toggle
from app.core.scheduling.leader_election_handle import get_leader_election as _get_leader_election
from app.db.session import get_background_session
from app.modules.accounts.repository import AccountsRepository
from app.modules.automations.repository import AutomationsRepository
from app.modules.automations.service import AutomationsService
from app.modules.request_logs.repository import RequestLogsRepository

if TYPE_CHECKING:
    from app.db.models import DashboardSettings

logger = logging.getLogger(__name__)

# Scheduler poll cadence (fixed; issue #1340 / PRINCIPLES.md P2). The
# scheduler keeps ``interval_seconds`` as a constructor field so tests can
# exercise the loop with a short interval.
_INTERVAL_SECONDS = 30


@dataclass(slots=True)
class AutomationsScheduler:
    interval_seconds: int
    enabled: bool
    # M2 background jobs: the loop always runs; each tick takes ONE settings
    # snapshot before the lock, skips while the effective
    # ``automations_scheduler_enabled`` is False, and hands that same snapshot to
    # the leader-gated body, so a dashboard pause applies on the next tick and
    # nothing re-reads the settings cache under ``_lock``.
    dashboard_snapshot: Callable[[], Awaitable[DashboardSettings]] = field(
        default_factory=lambda: _dashboard_settings_snapshot
    )
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def start(self) -> None:
        if not self.enabled:
            return
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
            # The whole tick, including the settings read that decides the
            # pause, is guarded: a transient database error must not kill the
            # loop task (a dead task also aborts the shutdown stop chain).
            try:
                await self._run_due_once()
            except Exception:
                logger.exception("Automations scheduler tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _run_due_once(self) -> None:
        snapshot = await self.dashboard_snapshot()
        if not resolve_background_job_toggle(snapshot, "automations_scheduler_enabled"):
            logger.debug("Automations scheduler skipped tick: paused in the dashboard settings")
            return
        await _get_leader_election().run_if_leader(partial(self._run_due_as_leader, snapshot))

    async def _run_due_as_leader(self, dashboard_settings: DashboardSettings) -> None:
        async with self._lock:
            try:
                async with get_background_session() as session:
                    repository = AutomationsRepository(session)
                    accounts_repository = AccountsRepository(session)
                    request_logs_repository = RequestLogsRepository(session)
                    service = AutomationsService(repository, accounts_repository, request_logs_repository)
                    await service.run_due_jobs(dashboard_settings=dashboard_settings)
            except Exception:
                logger.exception("Automations scheduler loop failed")


async def _dashboard_settings_snapshot() -> DashboardSettings:
    from app.core.config.settings_cache import get_settings_cache

    return await get_settings_cache().get()


def build_automations_scheduler() -> AutomationsScheduler:
    return AutomationsScheduler(
        interval_seconds=_INTERVAL_SECONDS,
        enabled=True,
    )
