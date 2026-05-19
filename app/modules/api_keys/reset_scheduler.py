from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Protocol, cast

from app.core.utils.time import utcnow
from app.db.session import get_background_session
from app.modules.api_keys.repository import ApiKeysRepository

logger = logging.getLogger(__name__)

_API_KEY_LIMIT_RESET_INTERVAL_SECONDS = 3600
_STALE_USAGE_RESERVATION_AGE = timedelta(hours=6)


class _LeaderElectionLike(Protocol):
    async def try_acquire(self) -> bool: ...


def _get_leader_election() -> _LeaderElectionLike:
    module = importlib.import_module("app.core.scheduling.leader_election")
    return cast(_LeaderElectionLike, module.get_leader_election())


@dataclass(slots=True)
class ApiKeyLimitResetScheduler:
    interval_seconds: int
    enabled: bool
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
            await self._reset_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _reset_once(self) -> None:
        if not await _get_leader_election().try_acquire():
            return
        async with self._lock:
            try:
                async with get_background_session() as session:
                    repo = ApiKeysRepository(session)
                    now = utcnow()
                    reset_count = await repo.reset_expired_limits(now=now)
                    if reset_count > 0:
                        logger.info("Reset expired API key limits reset_count=%s", reset_count)
                    released_count = await repo.release_stale_usage_reservations(
                        cutoff=now - _STALE_USAGE_RESERVATION_AGE
                    )
                    if released_count > 0:
                        logger.info("Released stale API key usage reservations released_count=%s", released_count)
            except Exception:
                logger.exception("API key limit reset loop failed")


def build_api_key_limit_reset_scheduler() -> ApiKeyLimitResetScheduler:
    return ApiKeyLimitResetScheduler(
        interval_seconds=_API_KEY_LIMIT_RESET_INTERVAL_SECONDS,
        enabled=True,
    )
