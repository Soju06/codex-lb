"""Write-behind coalescing for ``api_keys.last_used_at``.

Settlement paths record touches into a process-local map instead of writing
the column inside every reservation-settlement transaction (production showed
~616 such commits per 10 minutes for only 7 active keys). A replica-local
periodic flusher folds the pending map into the database in one transaction —
at most one guarded UPDATE per touched key per interval.

The flush is monotonic (greatest-wins): ``WHERE last_used_at IS NULL OR
last_used_at < :new`` never moves the stored value backwards, so concurrent
replicas can flush out of order without leader election. On a hard crash at
most one flush interval of ``last_used_at`` freshness is lost — accepted, the
column is display-only (dashboard ``lastUsedAt``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiKey
from app.db.session import get_background_session, sqlite_writer_section

logger = logging.getLogger(__name__)

FLUSH_INTERVAL_SECONDS = 30


class ApiKeyLastUsedCoalescer:
    """Process-local pending map of the newest observed used-at per API key.

    Only touched from the single asyncio event loop, so plain dict mutation
    is safe; ``record`` keeps the per-key maximum and ``flush`` swaps the map
    before writing so records arriving mid-flush land in the next interval.
    """

    def __init__(self) -> None:
        self._pending: dict[str, datetime] = {}

    def record(self, key_id: str, used_at: datetime) -> None:
        current = self._pending.get(key_id)
        if current is None or current < used_at:
            self._pending[key_id] = used_at

    def pending_snapshot(self) -> dict[str, datetime]:
        return dict(self._pending)

    def clear(self) -> None:
        self._pending.clear()

    async def flush(self) -> int:
        """Write all pending touches in one transaction; returns keys flushed.

        On failure the swapped batch is merged back into the pending map
        (per-key maximum wins) so touches survive until a later flush. The
        restore intentionally catches ``BaseException``: cancellation raises
        ``asyncio.CancelledError`` (a ``BaseException`` since Python 3.8), and
        a cancelled in-flight flush must not drop the swapped batch.
        """
        if not self._pending:
            return 0
        batch, self._pending = self._pending, {}
        try:
            async with sqlite_writer_section(), get_background_session() as session:
                await self._apply_batch(session, batch)
                await session.commit()
        except BaseException:
            for key_id, used_at in batch.items():
                self.record(key_id, used_at)
            raise
        return len(batch)

    @staticmethod
    async def _apply_batch(session: AsyncSession, batch: dict[str, datetime]) -> None:
        for key_id, used_at in batch.items():
            await session.execute(
                update(ApiKey)
                .where(ApiKey.id == key_id)
                .where(or_(ApiKey.last_used_at.is_(None), ApiKey.last_used_at < used_at))
                .values(last_used_at=used_at)
            )


class ApiKeyLastUsedFlushScheduler:
    """Replica-local periodic flush loop for the last-used coalescer.

    Every replica flushes its own process-local pending map, so this loop
    MUST NOT be leader-gated; the monotonic guarded UPDATE keeps concurrent
    replica flushes safe. ``stop()`` signals the loop to exit and awaits it —
    it MUST NOT cancel the task, because cancelling an in-flight ``flush()``
    would inject ``CancelledError`` into the awaited DB call after the pending
    map was already swapped out — then performs the final graceful-shutdown
    flush.
    """

    def __init__(
        self,
        coalescer: ApiKeyLastUsedCoalescer,
        *,
        interval_seconds: float = FLUSH_INTERVAL_SECONDS,
    ) -> None:
        self._coalescer = coalescer
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task:
            self._stop.set()
            # No cancel(): the stop event wakes wait_for immediately and an
            # in-flight flush runs to completion instead of losing its batch.
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._flush_once()

    async def _run_loop(self) -> None:
        while not await self._wait_or_stop(self._interval_seconds):
            await self._flush_once()

    async def _wait_or_stop(self, delay_seconds: float) -> bool:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=delay_seconds)
        except asyncio.TimeoutError:
            return False
        return True

    async def _flush_once(self) -> None:
        try:
            await self._coalescer.flush()
        except Exception:
            logger.exception("API key last_used_at flush failed; touches retained for the next flush")


_api_key_last_used_coalescer = ApiKeyLastUsedCoalescer()


def get_api_key_last_used_coalescer() -> ApiKeyLastUsedCoalescer:
    return _api_key_last_used_coalescer


def build_api_key_last_used_flush_scheduler() -> ApiKeyLastUsedFlushScheduler:
    return ApiKeyLastUsedFlushScheduler(get_api_key_last_used_coalescer())
