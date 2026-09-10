from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.db.session import detach_session_objects
from app.modules.accounts.background_repository import BackgroundAccountsRepository
from app.modules.desktop_usage.projection import eligible_accounts, project_pool
from app.modules.proxy.repo_bundle import ProxyRepoFactory
from app.modules.proxy.types import RateLimitStatusPayloadData
from app.modules.usage.background_repository import BackgroundAdditionalUsageRepository, BackgroundUsageRepository
from app.modules.usage.mappers import usage_history_to_window_row
from app.modules.usage.updater import UsageUpdater

_REFRESH_TIMEOUT_SECONDS = 5.0


class DesktopUsageService:
    def __init__(self, repo_factory: ProxyRepoFactory) -> None:
        self._repo_factory = repo_factory

    async def get_rate_limit_payload(self) -> RateLimitStatusPayloadData:
        async with self._repo_factory() as repos:
            accounts = eligible_accounts(await repos.accounts.list_accounts())
            latest = await repos.usage.latest_by_account(window="primary")
            if repos.session is not None:
                detach_session_objects(repos.session)
        updater = UsageUpdater(
            BackgroundUsageRepository(), BackgroundAccountsRepository(), BackgroundAdditionalUsageRepository()
        )
        try:
            async with asyncio.timeout(_REFRESH_TIMEOUT_SECONDS):
                await updater.refresh_accounts(accounts, latest, own_singleflight_sessions=True, join_existing=True)
        except TimeoutError:
            # Shared refreshes own their sessions; only stop waiting here.
            # The projector still rejects incomplete or stale persisted rows.
            pass
        async with self._repo_factory() as repos:
            accounts = eligible_accounts(await repos.accounts.list_accounts())
            windows = {}
            for window in ("primary", "secondary", "monthly"):
                latest = await repos.usage.latest_by_account(window=window)
                windows[window] = [usage_history_to_window_row(entry) for entry in latest.values()]
            additional = {}
            account_ids = [account.id for account in accounts]
            if account_ids:
                for key in await repos.additional_usage.list_limit_names(account_ids=account_ids):
                    additional[key] = {}
                    for window in ("primary", "secondary"):
                        entries = await repos.additional_usage.latest_by_account(
                            limit_name=key, window=window, account_ids=account_ids
                        )
                        additional[key][window] = list(entries.values())
            return project_pool(accounts, windows, additional, now=datetime.now(timezone.utc))
