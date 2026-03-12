from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from datetime import timedelta

from app.core.config.settings import get_settings
from app.core.utils.time import utcnow
from app.db.session import get_background_session
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.settings.repository import SettingsRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class StickySessionCleanupScheduler:
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
            await self._cleanup_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _cleanup_once(self) -> None:
        async with self._lock:
            try:
                async with get_background_session() as session:
                    settings_repo = SettingsRepository(session)
                    sticky_repo = StickySessionsRepository(session)
                    settings = await settings_repo.get_or_create()
                    cutoff = utcnow() - timedelta(seconds=settings.openai_cache_affinity_max_age_seconds)
                    deleted_count = await sticky_repo.purge_prompt_cache_before(cutoff)
                    if deleted_count > 0:
                        logger.info("Purged stale prompt-cache sticky sessions deleted_count=%s", deleted_count)
            except Exception:
                logger.exception("Sticky session cleanup loop failed")


def build_sticky_session_cleanup_scheduler() -> StickySessionCleanupScheduler:
    settings = get_settings()
    return StickySessionCleanupScheduler(
        interval_seconds=settings.sticky_session_cleanup_interval_seconds,
        enabled=True,
    )
