from __future__ import annotations

import time
from dataclasses import dataclass

from app.core.balancer import (
    AccountState,
    handle_permanent_failure,
    handle_quota_exceeded,
    handle_rate_limit,
    select_account,
)
from app.core.balancer.types import UpstreamError
from app.db.models import Account, AccountStatus
from app.modules.accounts.repository import AccountsRepository
from app.modules.usage.repository import UsageRepository
from app.modules.proxy.usage_updater import UsageUpdater


@dataclass
class RuntimeState:
    reset_at: int | None = None
    last_error_at: float | None = None
    last_selected_at: float | None = None
    error_count: int = 0


@dataclass
class AccountSelection:
    account: Account | None
    error_message: str | None


class LoadBalancer:
    def __init__(self, accounts_repo: AccountsRepository, usage_repo: UsageRepository) -> None:
        self._accounts_repo = accounts_repo
        self._usage_repo = usage_repo
        self._usage_updater = UsageUpdater(usage_repo, accounts_repo)
        self._runtime: dict[str, RuntimeState] = {}

    async def select_account(self) -> AccountSelection:
        accounts = await self._accounts_repo.list_accounts()
        latest_primary = await self._usage_repo.latest_by_account()
        await self._usage_updater.refresh_accounts(accounts, latest_primary)
        latest_primary = await self._usage_repo.latest_by_account()
        latest_secondary = await self._usage_repo.latest_by_account(window="secondary")

        states: list[AccountState] = []
        account_map: dict[str, Account] = {}

        for account in accounts:
            runtime = self._runtime.setdefault(account.id, RuntimeState())
            primary_entry = latest_primary.get(account.id)
            secondary_entry = latest_secondary.get(account.id)
            status = account.status
            used_percent = primary_entry.used_percent if primary_entry else None
            reset_at = runtime.reset_at

            secondary_used = (
                secondary_entry.used_percent if secondary_entry is not None else None
            )
            secondary_reset = secondary_entry.reset_at if secondary_entry else None
            if (
                status not in (AccountStatus.DEACTIVATED, AccountStatus.PAUSED)
                and secondary_used is not None
                and secondary_used >= 100.0
            ):
                status = AccountStatus.QUOTA_EXCEEDED
                used_percent = 100.0
                if secondary_reset is not None:
                    reset_at = secondary_reset
            elif status == AccountStatus.QUOTA_EXCEEDED and secondary_reset is not None:
                reset_at = secondary_reset
            state = AccountState(
                account_id=account.id,
                status=status,
                used_percent=used_percent,
                reset_at=reset_at,
                last_error_at=runtime.last_error_at,
                last_selected_at=runtime.last_selected_at,
                error_count=runtime.error_count,
                deactivation_reason=account.deactivation_reason,
            )
            states.append(state)
            account_map[account.id] = account

        result = select_account(states)
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
