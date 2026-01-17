from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

from app.core.balancer import (
    AccountState,
    SelectionResult,
    handle_permanent_failure,
    handle_quota_exceeded,
    handle_rate_limit,
    select_account,
)
from app.core.balancer.types import UpstreamError
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.proxy.usage_updater import UsageUpdater
from app.modules.usage.repository import UsageRepository


@dataclass
class RuntimeState:
    reset_at: float | None = None
    last_error_at: float | None = None
    last_selected_at: float | None = None
    error_count: int = 0


@dataclass
class AccountSelection:
    account: Account | None
    error_message: str | None


class LoadBalancer:
    def __init__(
        self,
        accounts_repo: AccountsRepository,
        usage_repo: UsageRepository,
        sticky_repo: StickySessionsRepository | None = None,
    ) -> None:
        self._accounts_repo = accounts_repo
        self._usage_repo = usage_repo
        self._usage_updater = UsageUpdater(usage_repo, accounts_repo)
        self._sticky_repo = sticky_repo
        self._runtime: dict[str, RuntimeState] = {}

    async def select_account(
        self,
        sticky_key: str | None = None,
        *,
        reallocate_sticky: bool = False,
    ) -> AccountSelection:
        accounts = await self._accounts_repo.list_accounts()
        latest_primary = await self._usage_repo.latest_by_account()
        await self._usage_updater.refresh_accounts(accounts, latest_primary)
        latest_primary = await self._usage_repo.latest_by_account()
        latest_secondary = await self._usage_repo.latest_by_account(window="secondary")

        states, account_map = _build_states(
            accounts=accounts,
            latest_primary=latest_primary,
            latest_secondary=latest_secondary,
            runtime=self._runtime,
        )

        result = await self._select_with_stickiness(
            states=states,
            account_map=account_map,
            sticky_key=sticky_key,
            reallocate_sticky=reallocate_sticky,
        )
        for state in states:
            account = account_map.get(state.account_id)
            if account:
                await self._sync_state(account, state)

        if result.account is None:
            return AccountSelection(account=None, error_message=result.error_message)

        selected = account_map.get(result.account.account_id)
        if selected:
            selected.status = result.account.status
            selected.deactivation_reason = result.account.deactivation_reason
            runtime = self._runtime.setdefault(selected.id, RuntimeState())
            runtime.last_selected_at = time.time()
        if selected is None:
            return AccountSelection(account=None, error_message=result.error_message)
        return AccountSelection(account=selected, error_message=None)

    async def _select_with_stickiness(
        self,
        *,
        states: list[AccountState],
        account_map: dict[str, Account],
        sticky_key: str | None,
        reallocate_sticky: bool,
    ) -> SelectionResult:
        if not sticky_key or not self._sticky_repo:
            return select_account(states)

        if reallocate_sticky:
            chosen = select_account(states)
            if chosen.account is not None and chosen.account.account_id in account_map:
                await self._sticky_repo.upsert(sticky_key, chosen.account.account_id)
            return chosen

        existing = await self._sticky_repo.get_account_id(sticky_key)
        if existing:
            pinned = next((state for state in states if state.account_id == existing), None)
            if pinned is None:
                await self._sticky_repo.delete(sticky_key)
            else:
                pinned_result = select_account([pinned])
                if pinned_result.account is not None:
                    return pinned_result

        chosen = select_account(states)
        if chosen.account is not None and chosen.account.account_id in account_map:
            await self._sticky_repo.upsert(sticky_key, chosen.account.account_id)
        return chosen

    async def mark_rate_limit(self, account: Account, error: UpstreamError) -> None:
        state = self._state_for(account)
        handle_rate_limit(state, error)
        await self._sync_state(account, state)

    async def mark_quota_exceeded(self, account: Account, error: UpstreamError) -> None:
        state = self._state_for(account)
        handle_quota_exceeded(state, error)
        await self._sync_state(account, state)

    async def mark_permanent_failure(self, account: Account, error_code: str) -> None:
        state = self._state_for(account)
        handle_permanent_failure(state, error_code)
        await self._sync_state(account, state)

    async def record_error(self, account: Account) -> None:
        state = self._state_for(account)
        state.error_count += 1
        state.last_error_at = time.time()
        await self._sync_state(account, state)

    def _state_for(self, account: Account) -> AccountState:
        runtime = self._runtime.setdefault(account.id, RuntimeState())
        return AccountState(
            account_id=account.id,
            status=account.status,
            used_percent=None,
            reset_at=runtime.reset_at,
            last_error_at=runtime.last_error_at,
            last_selected_at=runtime.last_selected_at,
            error_count=runtime.error_count,
            deactivation_reason=account.deactivation_reason,
        )

    async def _sync_state(self, account: Account, state: AccountState) -> None:
        runtime = self._runtime.setdefault(account.id, RuntimeState())
        runtime.reset_at = state.reset_at
        runtime.last_error_at = state.last_error_at
        runtime.error_count = state.error_count

        if account.status != state.status or account.deactivation_reason != state.deactivation_reason:
            await self._accounts_repo.update_status(
                account.id,
                state.status,
                state.deactivation_reason,
            )
            account.status = state.status
            account.deactivation_reason = state.deactivation_reason


def _build_states(
    *,
    accounts: Iterable[Account],
    latest_primary: dict[str, UsageHistory],
    latest_secondary: dict[str, UsageHistory],
    runtime: dict[str, RuntimeState],
) -> tuple[list[AccountState], dict[str, Account]]:
    states: list[AccountState] = []
    account_map: dict[str, Account] = {}

    for account in accounts:
        state = _state_from_account(
            account=account,
            primary_entry=latest_primary.get(account.id),
            secondary_entry=latest_secondary.get(account.id),
            runtime=runtime.setdefault(account.id, RuntimeState()),
        )
        states.append(state)
        account_map[account.id] = account
    return states, account_map


def _state_from_account(
    *,
    account: Account,
    primary_entry: UsageHistory | None,
    secondary_entry: UsageHistory | None,
    runtime: RuntimeState,
) -> AccountState:
    primary_used = primary_entry.used_percent if primary_entry else None
    primary_reset = primary_entry.reset_at if primary_entry else None
    secondary_used = secondary_entry.used_percent if secondary_entry else None
    secondary_reset = secondary_entry.reset_at if secondary_entry else None

    effective_reset_at = runtime.reset_at
    if account.status == AccountStatus.RATE_LIMITED and primary_reset is not None:
        primary_reset_at = float(primary_reset)
        if effective_reset_at is None:
            effective_reset_at = primary_reset_at
        else:
            effective_reset_at = min(effective_reset_at, primary_reset_at)

    status, used_percent, reset_at = _apply_secondary_quota(
        status=account.status,
        primary_used=primary_used,
        runtime_reset=effective_reset_at,
        secondary_used=secondary_used,
        secondary_reset=secondary_reset,
    )

    return AccountState(
        account_id=account.id,
        status=status,
        used_percent=used_percent,
        reset_at=reset_at,
        last_error_at=runtime.last_error_at,
        last_selected_at=runtime.last_selected_at,
        error_count=runtime.error_count,
        deactivation_reason=account.deactivation_reason,
    )


def _apply_secondary_quota(
    *,
    status: AccountStatus,
    primary_used: float | None,
    runtime_reset: float | None,
    secondary_used: float | None,
    secondary_reset: int | None,
) -> tuple[AccountStatus, float | None, float | None]:
    used_percent = primary_used
    reset_at = runtime_reset

    if status in (AccountStatus.DEACTIVATED, AccountStatus.PAUSED):
        return status, used_percent, reset_at

    if secondary_used is None:
        if status == AccountStatus.QUOTA_EXCEEDED and secondary_reset is not None:
            reset_at = secondary_reset
        return status, used_percent, reset_at

    if secondary_used >= 100.0:
        status = AccountStatus.QUOTA_EXCEEDED
        used_percent = 100.0
        if secondary_reset is not None:
            reset_at = secondary_reset
        return status, used_percent, reset_at

    if status == AccountStatus.QUOTA_EXCEEDED:
        status = AccountStatus.ACTIVE
        reset_at = None

    return status, used_percent, reset_at
