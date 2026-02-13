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
from app.core.openai.model_registry import get_model_registry
from app.core.usage.quota import apply_usage_quota
from app.db.models import Account, UsageHistory
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.repo_bundle import ProxyRepoFactory
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.usage.updater import UsageUpdater


@dataclass
class RuntimeState:
    reset_at: float | None = None
    cooldown_until: float | None = None
    last_error_at: float | None = None
    last_selected_at: float | None = None
    error_count: int = 0


@dataclass
class AccountSelection:
    account: Account | None
    error_message: str | None


class LoadBalancer:
    def __init__(self, repo_factory: ProxyRepoFactory) -> None:
        self._repo_factory = repo_factory
        self._runtime: dict[str, RuntimeState] = {}

    async def select_account(
        self,
        sticky_key: str | None = None,
        *,
        reallocate_sticky: bool = False,
        prefer_earlier_reset_accounts: bool = False,
        model: str | None = None,
    ) -> AccountSelection:
        selected_snapshot: Account | None = None
        error_message: str | None = None
        async with self._repo_factory() as repos:
            accounts = await repos.accounts.list_accounts()
            if model:
                accounts = _filter_accounts_for_model(accounts, model)
                if not accounts:
                    return AccountSelection(
                        account=None,
                        error_message=f"No accounts with a plan supporting model '{model}'",
                    )
            latest_primary = await repos.usage.latest_by_account()
            updater = UsageUpdater(repos.usage, repos.accounts)
            refreshed = await updater.refresh_accounts(accounts, latest_primary)
            if refreshed:
                latest_primary = await repos.usage.latest_by_account()
            latest_secondary = await repos.usage.latest_by_account(window="secondary")

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
                prefer_earlier_reset_accounts=prefer_earlier_reset_accounts,
                sticky_repo=repos.sticky_sessions,
            )
            for state in states:
                account = account_map.get(state.account_id)
                if account:
                    await self._sync_state(repos.accounts, account, state)

            if result.account is None:
                error_message = result.error_message
            else:
                selected = account_map.get(result.account.account_id)
                if selected is None:
                    error_message = result.error_message
                else:
                    selected.status = result.account.status
                    selected.deactivation_reason = result.account.deactivation_reason
                    selected_snapshot = _clone_account(selected)

        if selected_snapshot is None:
            return AccountSelection(account=None, error_message=error_message)

        runtime = self._runtime.setdefault(selected_snapshot.id, RuntimeState())
        runtime.last_selected_at = time.time()
        return AccountSelection(account=selected_snapshot, error_message=None)

    async def _select_with_stickiness(
        self,
        *,
        states: list[AccountState],
        account_map: dict[str, Account],
        sticky_key: str | None,
        reallocate_sticky: bool,
        prefer_earlier_reset_accounts: bool,
        sticky_repo: StickySessionsRepository | None,
    ) -> SelectionResult:
        if not sticky_key or not sticky_repo:
            return select_account(states, prefer_earlier_reset=prefer_earlier_reset_accounts)

        if reallocate_sticky:
            chosen = select_account(states, prefer_earlier_reset=prefer_earlier_reset_accounts)
            if chosen.account is not None and chosen.account.account_id in account_map:
                await sticky_repo.upsert(sticky_key, chosen.account.account_id)
            return chosen

        existing = await sticky_repo.get_account_id(sticky_key)
        if existing:
            pinned = next((state for state in states if state.account_id == existing), None)
            if pinned is None:
                await sticky_repo.delete(sticky_key)
            else:
                pinned_result = select_account([pinned], prefer_earlier_reset=prefer_earlier_reset_accounts)
                if pinned_result.account is not None:
                    return pinned_result

        chosen = select_account(states, prefer_earlier_reset=prefer_earlier_reset_accounts)
        if chosen.account is not None and chosen.account.account_id in account_map:
            await sticky_repo.upsert(sticky_key, chosen.account.account_id)
        return chosen

    async def mark_rate_limit(self, account: Account, error: UpstreamError) -> None:
        state = self._state_for(account)
        handle_rate_limit(state, error)
        async with self._repo_factory() as repos:
            await self._sync_state(repos.accounts, account, state)

    async def mark_quota_exceeded(self, account: Account, error: UpstreamError) -> None:
        state = self._state_for(account)
        handle_quota_exceeded(state, error)
        async with self._repo_factory() as repos:
            await self._sync_state(repos.accounts, account, state)

    async def mark_permanent_failure(self, account: Account, error_code: str) -> None:
        state = self._state_for(account)
        handle_permanent_failure(state, error_code)
        async with self._repo_factory() as repos:
            await self._sync_state(repos.accounts, account, state)

    async def record_error(self, account: Account) -> None:
        state = self._state_for(account)
        state.error_count += 1
        state.last_error_at = time.time()
        async with self._repo_factory() as repos:
            await self._sync_state(repos.accounts, account, state)

    def _state_for(self, account: Account) -> AccountState:
        runtime = self._runtime.setdefault(account.id, RuntimeState())
        return AccountState(
            account_id=account.id,
            status=account.status,
            used_percent=None,
            reset_at=runtime.reset_at,
            cooldown_until=runtime.cooldown_until,
            secondary_used_percent=None,
            secondary_reset_at=None,
            last_error_at=runtime.last_error_at,
            last_selected_at=runtime.last_selected_at,
            error_count=runtime.error_count,
            deactivation_reason=account.deactivation_reason,
        )

    async def _sync_state(
        self,
        accounts_repo: AccountsRepository,
        account: Account,
        state: AccountState,
    ) -> None:
        runtime = self._runtime.setdefault(account.id, RuntimeState())
        runtime.reset_at = state.reset_at
        runtime.cooldown_until = state.cooldown_until
        runtime.last_error_at = state.last_error_at
        runtime.error_count = state.error_count

        reset_at_int = int(state.reset_at) if state.reset_at else None
        status_changed = account.status != state.status
        reason_changed = account.deactivation_reason != state.deactivation_reason
        reset_changed = account.reset_at != reset_at_int

        if status_changed or reason_changed or reset_changed:
            await accounts_repo.update_status(
                account.id,
                state.status,
                state.deactivation_reason,
                reset_at_int,
            )
            account.status = state.status
            account.deactivation_reason = state.deactivation_reason
            account.reset_at = reset_at_int


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
    primary_window_minutes = primary_entry.window_minutes if primary_entry else None
    secondary_used = secondary_entry.used_percent if secondary_entry else None
    secondary_reset = secondary_entry.reset_at if secondary_entry else None

    # Use account.reset_at from DB as the authoritative source for runtime reset
    # This survives across requests since LoadBalancer is instantiated per-request
    db_reset_at = float(account.reset_at) if account.reset_at else None
    effective_runtime_reset = db_reset_at or runtime.reset_at

    status, used_percent, reset_at = apply_usage_quota(
        status=account.status,
        primary_used=primary_used,
        primary_reset=primary_reset,
        primary_window_minutes=primary_window_minutes,
        runtime_reset=effective_runtime_reset,
        secondary_used=secondary_used,
        secondary_reset=secondary_reset,
    )

    return AccountState(
        account_id=account.id,
        status=status,
        used_percent=used_percent,
        reset_at=reset_at,
        cooldown_until=runtime.cooldown_until,
        secondary_used_percent=secondary_used,
        secondary_reset_at=secondary_reset,
        last_error_at=runtime.last_error_at,
        last_selected_at=runtime.last_selected_at,
        error_count=runtime.error_count,
        deactivation_reason=account.deactivation_reason,
    )


def _filter_accounts_for_model(accounts: list[Account], model: str) -> list[Account]:
    allowed_plans = get_model_registry().plan_types_for_model(model)
    if allowed_plans is None:
        return accounts
    return [a for a in accounts if a.plan_type in allowed_plans]


def _clone_account(account: Account) -> Account:
    data = {column.name: getattr(account, column.name) for column in Account.__table__.columns}
    return Account(**data)
