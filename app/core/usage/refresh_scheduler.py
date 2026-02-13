from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field

from app.core.config.settings import get_settings
from app.db.session import SessionLocal, _safe_close, _safe_rollback
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.usage.repository import UsageRepository
from app.modules.usage.updater import UsageUpdater

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class UsageRefreshScheduler:
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
            await self._refresh_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _refresh_once(self) -> None:
        async with self._lock:
            session = SessionLocal()
            try:
                usage_repo = UsageRepository(session)
                accounts_repo = AccountsRepository(session)
                latest_usage = await usage_repo.latest_by_account(window="primary")
                accounts = await accounts_repo.list_accounts()
                updater = UsageUpdater(usage_repo, accounts_repo)
                await updater.refresh_accounts(accounts, latest_usage)
                await get_rate_limit_headers_cache().invalidate()
            except Exception:
                logger.exception("Usage refresh loop failed")
            finally:
                if session.in_transaction():
                    await _safe_rollback(session)
                await _safe_close(session)


def build_usage_refresh_scheduler() -> UsageRefreshScheduler:
    settings = get_settings()
    return UsageRefreshScheduler(
        interval_seconds=settings.usage_refresh_interval_seconds,
        enabled=settings.usage_refresh_enabled,
    )
