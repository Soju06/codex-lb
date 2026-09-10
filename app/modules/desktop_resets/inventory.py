from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from app.core.clients.rate_limit_reset_credits import RateLimitResetCreditsSnapshot, build_snapshot, fetch_reset_credits
from app.core.crypto import TokenEncryptor
from app.db.models import Account
from app.db.session import detach_session_objects
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.background_repository import BackgroundAccountsRepository
from app.modules.accounts.repository import AccountsRepository
from app.modules.desktop_resets.projection import OwnedCredit, ResetPoolUnavailable, eligible, pool_credits
from app.modules.desktop_usage.identity import DesktopIdentity
from app.modules.proxy.repo_bundle import ProxyRepoFactory
from app.modules.rate_limit_reset_credits.store import get_rate_limit_reset_credits_store
from app.modules.usage.updater import _resolve_upstream_route_for_account

_FRESHNESS_SECONDS = 180
_REFRESH_TIMEOUT_SECONDS = 10.0
_REFRESH_CONCURRENCY = 4


class CreditInventory:
    def __init__(self, repo_factory: ProxyRepoFactory) -> None:
        self._repos = repo_factory
        self.store = get_rate_limit_reset_credits_store()

    @asynccontextmanager
    async def account_repo(self) -> AsyncIterator[AccountsRepository]:
        async with self._repos() as repos:
            yield repos.accounts

    async def accounts(self, identity: DesktopIdentity, *, pooled: bool) -> list[Account]:
        async with self._repos() as repos:
            accounts = [account for account in await repos.accounts.list_accounts() if eligible(account)]
            if not any(account.id == identity.account_id for account in accounts):
                raise ResetPoolUnavailable("The signed-in account is no longer eligible")
            if not pooled:
                accounts = [account for account in accounts if account.id == identity.account_id]
            if repos.session is not None:
                detach_session_objects(repos.session)
            return accounts

    async def load(
        self, identity: DesktopIdentity, *, pooled: bool, force: bool = False, cached_only: bool = False
    ) -> dict[str, RateLimitResetCreditsSnapshot]:
        accounts = await self.accounts(identity, pooled=pooled)
        missing = [
            account
            for account in accounts
            if force or self.store.get_fresh(account.id, max_age_seconds=_FRESHNESS_SECONDS) is None
        ]
        if missing and cached_only:
            raise ResetPoolUnavailable()
        if missing:
            semaphore = asyncio.Semaphore(_REFRESH_CONCURRENCY)

            async def refresh(account: Account) -> None:
                async with semaphore:
                    await self._refresh(account.id)

            try:
                async with asyncio.timeout(_REFRESH_TIMEOUT_SECONDS), asyncio.TaskGroup() as group:
                    for account in missing:
                        group.create_task(refresh(account))
            except (TimeoutError, ExceptionGroup):
                raise ResetPoolUnavailable() from None
        # Membership and deletion can change while upstream requests run.
        current = await self.accounts(identity, pooled=pooled)
        snapshots: dict[str, RateLimitResetCreditsSnapshot] = {}
        for account in current:
            snapshot = self.store.get_fresh(account.id, max_age_seconds=_FRESHNESS_SECONDS)
            if snapshot is None:
                raise ResetPoolUnavailable()
            snapshots[account.id] = snapshot
        return snapshots

    async def available(self, identity: DesktopIdentity, *, pooled: bool, force: bool = False) -> list[OwnedCredit]:
        snapshots = await self.load(identity, pooled=pooled, force=force)
        return pool_credits(snapshots, now=datetime.now(timezone.utc))

    async def _refresh(self, account_id: str) -> None:
        generation = self.store.generation(account_id)
        async with self._repos() as repos:
            account = await repos.accounts.get_by_id(account_id)
            if account is None or not eligible(account):
                raise ResetPoolUnavailable()
            if repos.session is not None:
                detach_session_objects(repos.session)
        account = await AuthManager(BackgroundAccountsRepository()).ensure_fresh(account)
        if not eligible(account):
            raise ResetPoolUnavailable()
        route = await _resolve_upstream_route_for_account(account, operation="usage_refresh")
        token = TokenEncryptor().decrypt(account.access_token_encrypted)
        upstream_account_id = account.chatgpt_account_id
        result = await fetch_reset_credits(
            token,
            upstream_account_id,
            route=route,
            allow_direct_egress=route is None,
        )
        await self.store.set_if_generation(account_id, build_snapshot(result), generation)
