from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field

from app.core.config.settings import get_settings
from app.core.utils.time import utcnow
from app.db.session import get_background_session
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.usage.burnrate import compute_burn_rate_snapshot
from app.modules.usage.repository import AdditionalUsageRepository, BurnRateHistoryRepository, UsageRepository
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
            try:
                async with get_background_session() as session:
                    usage_repo = UsageRepository(session)
                    accounts_repo = AccountsRepository(session)
                    additional_usage_repo = AdditionalUsageRepository(session)
                    latest_usage = await usage_repo.latest_by_account(window="primary")
                    accounts = await accounts_repo.list_accounts()
                    updater = UsageUpdater(usage_repo, accounts_repo, additional_usage_repo)
                    await updater.refresh_accounts(accounts, latest_usage)

                    accounts = await accounts_repo.list_accounts()
                    latest_primary = await usage_repo.latest_by_account(window="primary")
                    latest_secondary = await usage_repo.latest_by_account(window="secondary")
                    burn_snapshot = compute_burn_rate_snapshot(
                        accounts=accounts,
                        latest_primary_usage=latest_primary,
                        latest_secondary_usage=latest_secondary,
                        now=utcnow(),
                    )
                    burn_rate_repo = BurnRateHistoryRepository(session)
                    await burn_rate_repo.add_entry(
                        primary_projected_plus_accounts=burn_snapshot.primary.projected_plus_accounts,
                        secondary_projected_plus_accounts=burn_snapshot.secondary.projected_plus_accounts,
                        primary_used_plus_accounts=burn_snapshot.primary.used_plus_accounts,
                        secondary_used_plus_accounts=burn_snapshot.secondary.used_plus_accounts,
                        primary_window_minutes=burn_snapshot.primary.window_minutes,
                        secondary_window_minutes=burn_snapshot.secondary.window_minutes,
                        primary_account_count=burn_snapshot.primary.included_account_count,
                        secondary_account_count=burn_snapshot.secondary.included_account_count,
                        primary_max_plus_equivalent_accounts=burn_snapshot.primary.max_plus_equivalent_accounts,
                        secondary_max_plus_equivalent_accounts=burn_snapshot.secondary.max_plus_equivalent_accounts,
                        recorded_at=burn_snapshot.recorded_at,
                    )

                    await get_rate_limit_headers_cache().invalidate()
            except Exception:
                logger.exception("Usage refresh loop failed")


def build_usage_refresh_scheduler() -> UsageRefreshScheduler:
    settings = get_settings()
    return UsageRefreshScheduler(
        interval_seconds=settings.usage_refresh_interval_seconds,
        enabled=settings.usage_refresh_enabled,
    )
