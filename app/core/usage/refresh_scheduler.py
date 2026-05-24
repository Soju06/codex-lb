from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
from dataclasses import dataclass, field
from typing import Protocol, cast

from app.core.config.settings import get_settings
from app.db.session import get_background_session
from app.modules.accounts.repository import AccountsRepository
from app.modules.limit_warmup.repository import LimitWarmupRepository
from app.modules.limit_warmup.service import LimitWarmupService, StreamingLimitWarmupSender
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.settings.repository import SettingsRepository
from app.modules.usage import updater as usage_updater_module
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository
from app.modules.usage.updater import UsageUpdater

logger = logging.getLogger(__name__)


class _LeaderElectionLike(Protocol):
    async def try_acquire(self) -> bool: ...


def _get_leader_election() -> _LeaderElectionLike:
    module = importlib.import_module("app.core.scheduling.leader_election")
    return cast(_LeaderElectionLike, module.get_leader_election())


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
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await usage_updater_module._USAGE_REFRESH_SINGLEFLIGHT.cancel_all()

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            await self._refresh_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _refresh_once(self) -> None:
        if not await _get_leader_election().try_acquire():
            return
        async with self._lock:
            try:
                async with get_background_session() as session:
                    usage_repo = UsageRepository(session)
                    accounts_repo = AccountsRepository(session)
                    additional_usage_repo = AdditionalUsageRepository(session)
                    settings_repo = SettingsRepository(session)
                    warmup_repo = LimitWarmupRepository(session)
                    request_logs_repo = RequestLogsRepository(session)
                    before_primary = await usage_repo.latest_by_account(window="primary")
                    before_secondary = await usage_repo.latest_by_account(window="secondary")
                    accounts = await accounts_repo.list_accounts()
                    updater = UsageUpdater(usage_repo, accounts_repo, additional_usage_repo)
                    usage_written = await updater.refresh_accounts(accounts, before_primary)
                    if usage_written:
                        after_primary = await usage_repo.latest_by_account(window="primary")
                        after_secondary = await usage_repo.latest_by_account(window="secondary")
                        dashboard_settings = await settings_repo.get_or_create()
                        warmup_service = LimitWarmupService(
                            warmup_repo,
                            request_logs_repo,
                            sender=StreamingLimitWarmupSender(accounts_repo),
                        )
                        refreshed_accounts = await accounts_repo.list_accounts(refresh_existing=True)
                        await warmup_service.run_after_usage_refresh(
                            accounts=refreshed_accounts,
                            settings=dashboard_settings,
                            before_primary=before_primary,
                            before_secondary=before_secondary,
                            after_primary=after_primary,
                            after_secondary=after_secondary,
                        )
                    await get_rate_limit_headers_cache().invalidate()
                    get_account_selection_cache().invalidate()
            except Exception:
                logger.exception("Usage refresh loop failed")


def build_usage_refresh_scheduler() -> UsageRefreshScheduler:
    settings = get_settings()
    return UsageRefreshScheduler(
        interval_seconds=settings.usage_refresh_interval_seconds,
        enabled=settings.usage_refresh_enabled,
    )
