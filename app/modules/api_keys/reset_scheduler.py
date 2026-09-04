from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol, TypeVar, cast

from app.core.utils.time import utcnow
from app.db.models import LimitWindow
from app.db.session import get_background_session
from app.modules.api_keys.limit_windows import next_limit_reset
from app.modules.api_keys.repository import ApiKeysRepository

logger = logging.getLogger(__name__)

_API_KEY_LIMIT_RESET_INTERVAL_SECONDS = 3600
_DAILY_LIMIT_ALIGNMENT_HOUR_UTC = 23
_DAILY_LIMIT_ALIGNMENT_MINUTE_UTC = 50
_STALE_USAGE_RESERVATION_AGE = timedelta(hours=6)
# Hard ceiling on reservation lifetime regardless of heartbeat activity. This
# is the backstop for orphaned reservation heartbeats (issue #1594): a leaked
# heartbeat keeps refreshing ``updated_at`` forever, which would otherwise
# exempt its reservation from the stale cutoff above. No legitimate request
# holds a usage reservation anywhere near this long.
_MAX_USAGE_RESERVATION_AGE = timedelta(hours=24)


_T = TypeVar("_T")


class _LeaderElectionLike(Protocol):
    async def run_if_leader(self, fn: Callable[[], Awaitable[_T]]) -> _T | None: ...


def _get_leader_election() -> _LeaderElectionLike:
    module = importlib.import_module("app.core.scheduling.leader_election")
    return cast(_LeaderElectionLike, module.get_leader_election())


def seconds_until_daily_limit_alignment(now: datetime) -> float:
    next_run = now.replace(
        hour=_DAILY_LIMIT_ALIGNMENT_HOUR_UTC,
        minute=_DAILY_LIMIT_ALIGNMENT_MINUTE_UTC,
        second=0,
        microsecond=0,
    )
    if next_run < now:
        next_run += timedelta(days=1)
    return (next_run - now).total_seconds()


@dataclass(slots=True)
class ApiKeyLimitResetScheduler:
    interval_seconds: int
    enabled: bool
    _task: asyncio.Task[None] | None = None
    _daily_alignment_task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def start(self) -> None:
        if not self.enabled:
            return
        reset_running = self._task is not None and not self._task.done()
        alignment_running = self._daily_alignment_task is not None and not self._daily_alignment_task.done()
        if reset_running and alignment_running:
            return
        self._stop.clear()
        if not reset_running:
            self._task = asyncio.create_task(self._run_loop(), name="api-key-limit-reset-scheduler")
        if not alignment_running:
            self._daily_alignment_task = asyncio.create_task(
                self._run_daily_alignment_loop(),
                name="api-key-daily-limit-alignment-scheduler",
            )

    async def stop(self) -> None:
        tasks = [task for task in (self._task, self._daily_alignment_task) if task is not None]
        if not tasks:
            return
        self._stop.set()
        for task in tasks:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*tasks)
        self._task = None
        self._daily_alignment_task = None

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            await self._reset_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _run_daily_alignment_loop(self) -> None:
        while not self._stop.is_set():
            delay_seconds = seconds_until_daily_limit_alignment(utcnow())
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay_seconds)
            except asyncio.TimeoutError:
                await self._align_daily_limits_once()

    async def _reset_once(self) -> None:
        await _get_leader_election().run_if_leader(self._reset_as_leader)

    async def _align_daily_limits_once(self) -> None:
        await _get_leader_election().run_if_leader(self._align_daily_limits_as_leader)

    async def _reset_as_leader(self) -> None:
        async with self._lock:
            try:
                async with get_background_session() as session:
                    repo = ApiKeysRepository(session)
                    now = utcnow()
                    reset_count = await repo.reset_expired_limits(now=now)
                    if reset_count > 0:
                        logger.info("Reset expired API key limits reset_count=%s", reset_count)
                    released_count = await repo.release_stale_usage_reservations(
                        cutoff=now - _STALE_USAGE_RESERVATION_AGE,
                        max_age_cutoff=now - _MAX_USAGE_RESERVATION_AGE,
                    )
                    if released_count > 0:
                        logger.info("Released stale API key usage reservations released_count=%s", released_count)
            except Exception:
                logger.exception("API key limit reset loop failed")

    async def _align_daily_limits_as_leader(self) -> None:
        async with self._lock:
            try:
                async with get_background_session() as session:
                    now = utcnow()
                    reset_at = next_limit_reset(now, LimitWindow.DAILY)
                    aligned_count = await ApiKeysRepository(session).align_daily_limit_resets(reset_at=reset_at)
                    if aligned_count > 0:
                        logger.info(
                            "Aligned daily API key limits to UTC midnight aligned_count=%s reset_at=%s",
                            aligned_count,
                            reset_at.isoformat(),
                        )
            except Exception:
                logger.exception("Daily API key limit alignment failed")


def build_api_key_limit_reset_scheduler() -> ApiKeyLimitResetScheduler:
    return ApiKeyLimitResetScheduler(
        interval_seconds=_API_KEY_LIMIT_RESET_INTERVAL_SECONDS,
        enabled=True,
    )
