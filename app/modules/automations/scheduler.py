from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.core.config.background_jobs import background_job_enabled
from app.core.scheduling.leader_election_handle import get_leader_election as _get_leader_election
from app.db.session import get_background_session
from app.modules.accounts.repository import AccountsRepository
from app.modules.automations.repository import AutomationsRepository
from app.modules.automations.service import AutomationsService
from app.modules.request_logs.repository import RequestLogsRepository

logger = logging.getLogger(__name__)

# Scheduler poll cadence (fixed; issue #1340 / PRINCIPLES.md P2). The
# scheduler keeps ``interval_seconds`` as a constructor field so tests can
# exercise the loop with a short interval.
_INTERVAL_SECONDS = 30


@dataclass(slots=True)
class AutomationsScheduler:
    interval_seconds: int
    enabled: bool
    # M2 background jobs: the loop always runs; each tick reads the effective
    # ``automations_scheduler_enabled`` toggle from the settings cache and skips
    # while it is False, so a dashboard pause applies on the next tick.
    dashboard_enabled: Callable[[], Awaitable[bool]] = field(default_factory=lambda: _dashboard_automations_enabled)
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
            await self._run_due_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _run_due_once(self) -> None:
        if not await self.dashboard_enabled():
            logger.debug("Automations scheduler skipped tick: paused in the dashboard settings")
            return
        await _get_leader_election().run_if_leader(self._run_due_as_leader)

    async def _run_due_as_leader(self) -> None:
        async with self._lock:
            try:
                async with get_background_session() as session:
                    repository = AutomationsRepository(session)
                    accounts_repository = AccountsRepository(session)
                    request_logs_repository = RequestLogsRepository(session)
                    service = AutomationsService(repository, accounts_repository, request_logs_repository)
                    await service.run_due_jobs()
            except Exception:
                logger.exception("Automations scheduler loop failed")


async def _dashboard_automations_enabled() -> bool:
    return await background_job_enabled("automations_scheduler_enabled")


def build_automations_scheduler() -> AutomationsScheduler:
    return AutomationsScheduler(
        interval_seconds=_INTERVAL_SECONDS,
        enabled=True,
    )
