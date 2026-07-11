from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncIterator, Protocol, cast

from app.core.config.settings import get_settings
from app.core.usage import capacity_for_plan
from app.db.models import Account, AccountLimitWarmup, AccountStatus, UsageHistory
from app.db.session import detach_session_objects, get_background_session
from app.modules.accounts.background_repository import BackgroundAccountsRepository
from app.modules.accounts.repository import AccountsRepository
from app.modules.limit_warmup.repository import LimitWarmupRepository
from app.modules.limit_warmup.service import LimitWarmupService, StreamingLimitWarmupSender
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.proxy.load_balancer import background_recovery_state_from_account
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.settings.repository import SettingsRepository
from app.modules.usage import updater as usage_updater_module
from app.modules.usage.repository import UsageRepository
from app.modules.usage.updater import build_background_usage_updater

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


class _BackgroundLimitWarmupRepository:
    async def latest_by_account(self, account_ids: list[str]) -> dict[str, AccountLimitWarmup]:
        async with get_background_session() as session:
            attempts = await LimitWarmupRepository(session).latest_by_account(account_ids)
            detach_session_objects(session)
            return attempts

    async def try_create_attempt(
        self,
        *,
        account_id: str,
        window: str,
        reset_at: int,
        model: str,
        attempted_at: datetime,
        status: str = "pending",
    ) -> AccountLimitWarmup | None:
        async with get_background_session() as session:
            attempt = await LimitWarmupRepository(session).try_create_attempt(
                account_id=account_id,
                window=window,
                reset_at=reset_at,
                model=model,
                attempted_at=attempted_at,
                status=status,
            )
            detach_session_objects(session)
            return attempt

    async def complete_attempt(
        self,
        attempt_id: int,
        *,
        status: str,
        completed_at: datetime,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AccountLimitWarmup | None:
        async with get_background_session() as session:
            attempt = await LimitWarmupRepository(session).complete_attempt(
                attempt_id,
                status=status,
                completed_at=completed_at,
                error_code=error_code,
                error_message=error_message,
            )
            detach_session_objects(session)
            return attempt


class _BackgroundRequestLogsRepository:
    async def add_log(self, *args: Any, **kwargs: Any) -> object:
        async with get_background_session() as session:
            return await RequestLogsRepository(session).add_log(*args, **kwargs)


def _get_leader_election() -> _LeaderElectionLike:
    module = importlib.import_module("app.core.scheduling.leader_election")
    return cast(_LeaderElectionLike, module.get_leader_election())


@dataclass(slots=True)
class UsageRefreshScheduler:
    interval_seconds: int
    enabled: bool
    _next_account_index: int = 0
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
            started_at = time.monotonic()
            delay = await self._refresh_once()
            remaining_delay = max(0.0, delay - (time.monotonic() - started_at))
            if remaining_delay <= 0:
                continue
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=remaining_delay)
            except asyncio.TimeoutError:
                continue

    async def _refresh_once(self) -> float:
        if not await _get_leader_election().try_acquire():
            return float(self.interval_seconds)
        async with self._lock:
            account_count = 0
            try:
                async with get_background_session() as session:
                    usage_repo = UsageRepository(session)
                    accounts_repo = AccountsRepository(session)
                    before_primary = await usage_repo.latest_by_account(window="primary")
                    before_secondary = await usage_repo.latest_by_account(window="secondary")
                    accounts = _ordered_usage_refresh_accounts(await accounts_repo.list_accounts())
                    selected_account, cycle_complete = self._select_next_account(accounts)
                    detach_session_objects(session)
                account_count = len(accounts)
                if selected_account is None:
                    await _invalidate_usage_refresh_caches()
                    return float(self.interval_seconds)

                updater = build_background_usage_updater()
                refresh_started_at = usage_updater_module.utcnow()
                usage_written = await updater.refresh_accounts([selected_account], before_primary)
                if usage_written:
                    async with get_background_session() as session:
                        usage_repo = UsageRepository(session)
                        accounts_repo = AccountsRepository(session)
                        settings_repo = SettingsRepository(session)
                        after_primary = await usage_repo.latest_by_account(window="primary")
                        after_secondary = await usage_repo.latest_by_account(window="secondary")
                        dashboard_settings = await settings_repo.get_or_create()
                        refreshed_accounts = await accounts_repo.list_accounts(refresh_existing=True)
                        detach_session_objects(session)
                    warmup_service = LimitWarmupService(
                        cast(Any, _BackgroundLimitWarmupRepository()),
                        cast(Any, _BackgroundRequestLogsRepository()),
                        sender=StreamingLimitWarmupSender(
                            cast(AccountsRepository, BackgroundAccountsRepository()),
                            accounts_repo_factory=_background_accounts_repo,
                        ),
                    )
                    await warmup_service.run_after_usage_refresh(
                        accounts=refreshed_accounts,
                        settings=dashboard_settings,
                        before_primary=before_primary,
                        before_secondary=before_secondary,
                        after_primary=after_primary,
                        after_secondary=after_secondary,
                        refresh_started_at=refresh_started_at,
                        usage_refresh_interval_seconds=self.interval_seconds,
                    )
                    async with get_background_session() as session:
                        usage_repo = UsageRepository(session)
                        accounts_repo = AccountsRepository(session)
                        await reconcile_recoverable_account_statuses(
                            accounts_repo=accounts_repo,
                            usage_repo=usage_repo,
                            accounts=refreshed_accounts,
                        )
                if cycle_complete:
                    await _invalidate_usage_refresh_caches()
            except Exception:
                logger.exception("Usage refresh loop failed")
                return float(self.interval_seconds)
        return _usage_refresh_slice_seconds(self.interval_seconds, account_count)

    def _select_next_account(self, accounts: list[Account]) -> tuple[Account | None, bool]:
        if not accounts:
            self._next_account_index = 0
            return None, True
        index = self._next_account_index % len(accounts)
        next_index = (index + 1) % len(accounts)
        self._next_account_index = next_index
        return accounts[index], next_index == 0


def build_usage_refresh_scheduler() -> UsageRefreshScheduler:
    settings = get_settings()
    return UsageRefreshScheduler(
        interval_seconds=settings.usage_refresh_interval_seconds,
        enabled=settings.usage_refresh_enabled,
    )


def _ordered_usage_refresh_accounts(accounts: list[Account]) -> list[Account]:
    return sorted(
        (
            account
            for account in accounts
            if account.status not in (AccountStatus.PAUSED, AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED)
        ),
        key=lambda account: account.id,
    )


def _usage_refresh_slice_seconds(interval_seconds: int, account_count: int) -> float:
    if account_count <= 0:
        return float(interval_seconds)
    return float(interval_seconds) / account_count


async def _invalidate_usage_refresh_caches() -> None:
    await get_rate_limit_headers_cache().invalidate()
    get_account_selection_cache().invalidate()


@contextlib.asynccontextmanager
async def _background_accounts_repo() -> AsyncIterator[AccountsRepository]:
    async with get_background_session() as session:
        yield AccountsRepository(session)


async def reconcile_recoverable_account_statuses(
    *,
    accounts_repo: _RecoverableAccountsRepository,
    usage_repo: _LatestUsageRepository,
    accounts: list[Account],
) -> int:
    candidates = [account for account in accounts if account.status in _RECOVERABLE_ACCOUNT_STATUSES]
    if not candidates:
        return 0

    latest_primary = await usage_repo.latest_by_account(window="primary")
    latest_secondary = await usage_repo.latest_by_account(window="secondary")
    latest_monthly = await usage_repo.latest_by_account(window="monthly")

    recovered = 0
    for account in candidates:
        state = background_recovery_state_from_account(
            account=account,
            primary_entry=latest_primary.get(account.id),
            secondary_entry=_select_long_window_entry(
                account=account,
                monthly_entry=latest_monthly.get(account.id),
                secondary_entry=latest_secondary.get(account.id),
            ),
        )
        if state.status != AccountStatus.ACTIVE:
            continue
        reset_at = int(state.reset_at) if state.reset_at else None
        blocked_at = int(state.blocked_at) if state.blocked_at else None
        deactivation_reason = None
        if (
            state.status == account.status
            and deactivation_reason == account.deactivation_reason
            and reset_at == account.reset_at
            and blocked_at == account.blocked_at
        ):
            continue
        updated = await accounts_repo.update_status_if_current(
            account.id,
            state.status,
            deactivation_reason,
            reset_at,
            blocked_at=blocked_at,
            expected_status=account.status,
            expected_deactivation_reason=account.deactivation_reason,
            expected_reset_at=account.reset_at,
            expected_blocked_at=account.blocked_at,
        )
        if not updated:
            continue
        account.status = state.status
        account.deactivation_reason = deactivation_reason
        account.reset_at = reset_at
        account.blocked_at = blocked_at
        recovered += 1
    return recovered


def _select_long_window_entry(
    *,
    account: Account,
    monthly_entry: UsageHistory | None,
    secondary_entry: UsageHistory | None,
) -> UsageHistory | None:
    if monthly_entry is not None and capacity_for_plan(account.plan_type, "monthly") is not None:
        return monthly_entry
    return secondary_entry
