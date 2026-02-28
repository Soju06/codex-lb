from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field

from app.core.config.settings import get_settings
from app.db.session import get_background_session
from app.modules.proxy.response_context_repository import ResponseContextRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ResponseContextCleanupScheduler:
    interval_seconds: int
    enabled: bool
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)

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
        try:
            async with get_background_session() as session:
                repo = ResponseContextRepository(session)
                deleted_responses, deleted_items = await repo.delete_expired()
                if deleted_responses or deleted_items:
                    logger.info(
                        "response_context_cleanup deleted_responses=%s deleted_items=%s",
                        deleted_responses,
                        deleted_items,
                    )
        except Exception:
            logger.exception("response context cleanup loop failed")


def build_response_context_cleanup_scheduler() -> ResponseContextCleanupScheduler:
    settings = get_settings()
    return ResponseContextCleanupScheduler(
        interval_seconds=settings.response_context_cleanup_interval_seconds,
        enabled=settings.response_context_enable_durable and settings.response_context_cleanup_enabled,
    )

