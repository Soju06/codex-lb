from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
from dataclasses import dataclass, field
from typing import Protocol, cast

from app.core.config.settings import get_settings
from app.db.models import Account, AccountStatus, UsageHistory
from app.db.session import get_background_session
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.proxy.load_balancer import background_recovery_state_from_account
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.usage import updater as usage_updater_module
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository
from app.modules.usage.updater import UsageUpdater

logger = logging.getLogger(__name__)

_RECOVERABLE_ACCOUNT_STATUSES = frozenset({AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED})


class _LeaderElectionLike(Protocol):
    async def try_acquire(self) -> bool: ...


class _RecoverableAccountsRepository(Protocol):
    async def update_status_if_current(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        blocked_at: int | None | object = None,
        *,
        expected_status: AccountStatus,
        expected_deactivation_reason: str | None = None,
        expected_reset_at: int | None = None,
        expected_blocked_at: int | None | object = None,
    ) -> bool: ...


class _LatestUsageRepository(Protocol):
    async def latest_by_account(self, window: str | None = None) -> dict[str, UsageHistory]: ...


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
                    latest_usage = await usage_repo.latest_by_account(window="primary")
                    accounts = await accounts_repo.list_accounts()
                    updater = UsageUpdater(usage_repo, accounts_repo, additional_usage_repo)
                    await updater.refresh_accounts(accounts, latest_usage)
                    refreshed_accounts = await accounts_repo.list_accounts()
                    await reconcile_recoverable_account_statuses(
                        accounts_repo=accounts_repo,
                        usage_repo=usage_repo,
                        accounts=refreshed_accounts,
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


async def reconcile_recoverable_account_statuses(
    *,
    accounts_repo: _RecoverableAccountsRepository,
    usage_repo: _LatestUsageRepository,
    accounts: list[Account],
) -> int:
    candidates = [account for account in accounts if account.status in _RECOVERABLE_ACCOUNT_STATUSES]
    if not candidates:
        return 0

    latest_primary, latest_secondary = await asyncio.gather(
        usage_repo.latest_by_account(window="primary"),
        usage_repo.latest_by_account(window="secondary"),
    )

    recovered = 0
    for account in candidates:
        state = background_recovery_state_from_account(
            account=account,
            primary_entry=latest_primary.get(account.id),
            secondary_entry=latest_secondary.get(account.id),
        )
        if state.status != AccountStatus.ACTIVE:
            continue
        updated = await accounts_repo.update_status_if_current(
            account.id,
            AccountStatus.ACTIVE,
            None,
            None,
            blocked_at=None,
            expected_status=account.status,
            expected_deactivation_reason=account.deactivation_reason,
            expected_reset_at=account.reset_at,
            expected_blocked_at=account.blocked_at,
        )
        if not updated:
            continue
        account.status = AccountStatus.ACTIVE
        account.deactivation_reason = None
        account.reset_at = None
        account.blocked_at = None
        recovered += 1
    return recovered
