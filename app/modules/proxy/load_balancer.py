from __future__ import annotations

import logging
import time
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

import anyio

from app.core import usage as usage_core
from app.core.balancer import (
    AccountState,
    RoutingStrategy,
    SelectionResult,
    handle_permanent_failure,
    handle_quota_exceeded,
    handle_rate_limit,
    select_account,
)
from app.core.balancer.types import UpstreamError
from app.core.config.settings import get_settings
from app.core.openai.model_registry import get_model_registry
from app.core.usage.quota import apply_usage_quota
from app.core.usage.types import UsageWindowRow
from app.core.utils.time import utcnow
from app.db.models import Account, AdditionalUsageHistory, StickySessionKind, UsageHistory
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.additional_model_limits import get_additional_quota_key_for_model_id
from app.modules.proxy.repo_bundle import ProxyRepoFactory, ProxyRepositories
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.usage.additional_quota_keys import canonicalize_additional_quota_key

logger = logging.getLogger(__name__)

NO_PLAN_SUPPORT_FOR_MODEL = "no_plan_support_for_model"
ADDITIONAL_QUOTA_DATA_UNAVAILABLE = "additional_quota_data_unavailable"
NO_ADDITIONAL_QUOTA_ELIGIBLE_ACCOUNTS = "no_additional_quota_eligible_accounts"


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
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class _SelectionInputs:
    accounts: list[Account]
    latest_primary: dict[str, UsageHistory]
    latest_secondary: dict[str, UsageHistory]
    error_message: str | None = None
    error_code: str | None = None


class LoadBalancer:
    def __init__(self, repo_factory: ProxyRepoFactory) -> None:
        self._repo_factory = repo_factory
        self._runtime: dict[str, RuntimeState] = {}
        self._runtime_lock = anyio.Lock()

    async def select_account(
        self,
        sticky_key: str | None = None,
        *,
        sticky_kind: StickySessionKind | None = None,
        reallocate_sticky: bool = False,
        sticky_max_age_seconds: int | None = None,
        prefer_earlier_reset_accounts: bool = False,
        routing_strategy: RoutingStrategy = "usage_weighted",
        model: str | None = None,
        additional_limit_name: str | None = None,
        account_tags: Collection[str] | None = None,
        exclude_account_ids: Collection[str] | None = None,
    ) -> AccountSelection:
        selection_inputs = await self._load_selection_inputs(
            model=model,
            additional_limit_name=additional_limit_name,
            account_tags=account_tags,
        )
        excluded_ids = set(exclude_account_ids or ())
        if excluded_ids and selection_inputs.accounts:
            selection_inputs = _SelectionInputs(
                accounts=[account for account in selection_inputs.accounts if account.id not in excluded_ids],
                latest_primary=selection_inputs.latest_primary,
                latest_secondary=selection_inputs.latest_secondary,
                error_message=selection_inputs.error_message,
                error_code=selection_inputs.error_code,
            )
        if selection_inputs.error_code is not None and not selection_inputs.accounts:
            return AccountSelection(
                account=None,
                error_message=selection_inputs.error_message,
                error_code=selection_inputs.error_code,
            )

        selected_snapshot: Account | None = None
        error_message: str | None = None
        async with self._runtime_lock:
            async with self._repo_factory() as repos:
                self._prune_runtime(selection_inputs.accounts)

                states, account_map = _build_states(
                    accounts=selection_inputs.accounts,
                    latest_primary=selection_inputs.latest_primary,
                    latest_secondary=selection_inputs.latest_secondary,
                    runtime=self._runtime,
                )

                result = await self._select_with_stickiness(
                    states=states,
                    account_map=account_map,
                    sticky_key=sticky_key,
                    sticky_kind=sticky_kind,
                    reallocate_sticky=reallocate_sticky,
                    sticky_max_age_seconds=sticky_max_age_seconds,
                    prefer_earlier_reset_accounts=prefer_earlier_reset_accounts,
                    routing_strategy=routing_strategy,
                    sticky_repo=repos.sticky_sessions,
                )
                if result.account is not None:
                    runtime = self._runtime.setdefault(result.account.account_id, RuntimeState())
                    runtime.last_selected_at = time.time()

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
            logger.warning(
                "No account selected strategy=%s sticky=%s model=%s error=%s",
                routing_strategy,
                bool(sticky_key),
                model,
                error_message,
            )
            return AccountSelection(account=None, error_message=error_message, error_code=None)

        runtime = self._runtime.setdefault(selected_snapshot.id, RuntimeState())
        runtime.last_selected_at = time.time()
        logger.info(
            "Selected account_id=%s strategy=%s sticky=%s model=%s",
            selected_snapshot.id,
            routing_strategy,
            bool(sticky_key),
            model,
        )
        return AccountSelection(account=selected_snapshot, error_message=None, error_code=None)

    async def _load_selection_inputs(
        self,
        *,
        model: str | None,
        additional_limit_name: str | None = None,
        account_tags: Collection[str] | None = None,
    ) -> _SelectionInputs:
        async with self._repo_factory() as repos:
            all_accounts = await repos.accounts.list_accounts()
            effective_limit_name = additional_limit_name or _gated_limit_name_for_model(model)
            accounts = all_accounts
            if account_tags:
                accounts = _filter_accounts_for_tags(accounts, account_tags)
                if not accounts:
                    return _SelectionInputs(
                        accounts=[],
                        latest_primary={},
                        latest_secondary={},
                        error_message="No accounts match the API key tags",
                        error_code="no_accounts",
                    )
            pool_accounts = accounts
            if model and (effective_limit_name is None or _mapped_model_has_registry_entry(model)):
                accounts = _filter_accounts_for_model(accounts, model)
            if model and not accounts:
                if not pool_accounts:
                    return _SelectionInputs(
                        accounts=[],
                        latest_primary={},
                        latest_secondary={},
                    )
                return _SelectionInputs(
                    accounts=[],
                    latest_primary={},
                    latest_secondary={},
                    error_message=f"No accounts with a plan supporting model '{model}'",
                    error_code=NO_PLAN_SUPPORT_FOR_MODEL,
                )

            if effective_limit_name:
                accounts, error_code, error_message = await self._filter_accounts_for_additional_limit(
                    accounts,
                    model=model,
                    limit_name=effective_limit_name,
                    repos=repos,
                )
                if not accounts:
                    return _SelectionInputs(
                        accounts=[],
                        latest_primary={},
                        latest_secondary={},
                        error_message=error_message,
                        error_code=error_code,
                    )
            if not accounts:
                return _SelectionInputs(
                    accounts=[],
                    latest_primary={},
                    latest_secondary={},
                )

            latest_primary = await repos.usage.latest_by_account()
            latest_secondary = await repos.usage.latest_by_account(window="secondary")
            return _SelectionInputs(
                accounts=[_clone_account(account) for account in accounts],
                latest_primary={
                    account_id: _clone_usage_history(entry) for account_id, entry in latest_primary.items()
                },
                latest_secondary={
                    account_id: _clone_usage_history(entry) for account_id, entry in latest_secondary.items()
                },
            )

    async def _filter_accounts_for_additional_limit(
        self,
        accounts: list[Account],
        *,
        model: str | None,
        limit_name: str,
        repos: ProxyRepositories,
    ) -> tuple[list[Account], str | None, str | None]:
        if not accounts:
            return [], None, None

        fresh_since = _additional_usage_fresh_since()
        account_ids = [account.id for account in accounts]
        latest_primary = await _latest_additional_by_key(
            repos.additional_usage,
            limit_name,
            "primary",
            account_ids=account_ids,
        )
        latest_secondary = await _latest_additional_by_key(
            repos.additional_usage,
            limit_name,
            "secondary",
            account_ids=account_ids,
        )
        fresh_primary = await _latest_additional_by_key(
            repos.additional_usage,
            limit_name,
            "primary",
            account_ids=account_ids,
            since=fresh_since,
        )
        fresh_secondary = await _latest_additional_by_key(
            repos.additional_usage,
            limit_name,
            "secondary",
            account_ids=account_ids,
            since=fresh_since,
        )

        fresh_account_ids = set(fresh_primary) | set(fresh_secondary)

        eligible_accounts: list[Account] = []
        blocked_by_data = False
        for account in accounts:
            eligibility = _additional_quota_eligibility(
                account_id=account.id,
                latest_primary=latest_primary,
                latest_secondary=latest_secondary,
                fresh_primary=fresh_primary,
                fresh_secondary=fresh_secondary,
            )
            if eligibility == "eligible":
                eligible_accounts.append(account)
                continue
            if eligibility == "data_unavailable":
                blocked_by_data = True

        if not eligible_accounts:
            error_code = ADDITIONAL_QUOTA_DATA_UNAVAILABLE if blocked_by_data else NO_ADDITIONAL_QUOTA_ELIGIBLE_ACCOUNTS
            error_message = (
                f"No fresh additional quota data available for model '{model}'"
                if blocked_by_data
                else f"No accounts with available additional quota for model '{model}'"
            )
            logger.warning(
                (
                    "Blocked gated model routing model=%s limit_name=%s reason=%s "
                    "freshness_since=%s candidate_accounts=%s fresh_accounts=%s"
                ),
                model,
                limit_name,
                error_code,
                fresh_since.isoformat(),
                len(accounts),
                len(fresh_account_ids),
            )
            return ([], error_code, error_message)

        logger.info(
            (
                "Applied gated model routing model=%s limit_name=%s "
                "candidate_accounts=%s fresh_accounts=%s eligible_accounts=%s"
            ),
            model,
            limit_name,
            len(accounts),
            len(fresh_account_ids),
            len(eligible_accounts),
        )
        return eligible_accounts, None, None

    def _prune_runtime(self, accounts: Iterable[Account]) -> None:
        account_ids = {account.id for account in accounts}
        stale_ids = [account_id for account_id in self._runtime if account_id not in account_ids]
        for account_id in stale_ids:
            self._runtime.pop(account_id, None)

    async def _select_with_stickiness(
        self,
        *,
        states: list[AccountState],
        account_map: dict[str, Account],
        sticky_key: str | None,
        sticky_kind: StickySessionKind | None,
        reallocate_sticky: bool,
        sticky_max_age_seconds: int | None,
        prefer_earlier_reset_accounts: bool,
        routing_strategy: RoutingStrategy,
        sticky_repo: StickySessionsRepository | None,
    ) -> SelectionResult:
        if not sticky_key or not sticky_repo:
            return select_account(
                states,
                prefer_earlier_reset=prefer_earlier_reset_accounts,
                routing_strategy=routing_strategy,
            )
        if sticky_kind is None:
            raise ValueError("sticky_kind is required when sticky_key is provided")

        if reallocate_sticky:
            chosen = select_account(
                states,
                prefer_earlier_reset=prefer_earlier_reset_accounts,
                routing_strategy=routing_strategy,
            )
            if chosen.account is not None and chosen.account.account_id in account_map:
                await sticky_repo.upsert(sticky_key, chosen.account.account_id, kind=sticky_kind)
            return chosen

        existing = await sticky_repo.get_account_id(
            sticky_key,
            kind=sticky_kind,
            max_age_seconds=sticky_max_age_seconds,
        )
        if existing:
            pinned = next((state for state in states if state.account_id == existing), None)
            if pinned is None:
                await sticky_repo.delete(sticky_key, kind=sticky_kind)
            else:
                pinned_result = select_account(
                    [pinned],
                    prefer_earlier_reset=prefer_earlier_reset_accounts,
                    routing_strategy=routing_strategy,
                    allow_backoff_fallback=False,
                )
                if pinned_result.account is not None:
                    if sticky_max_age_seconds is not None:
                        await sticky_repo.upsert(sticky_key, pinned.account_id, kind=sticky_kind)
                    return pinned_result

        chosen = select_account(
            states,
            prefer_earlier_reset=prefer_earlier_reset_accounts,
            routing_strategy=routing_strategy,
        )
        if chosen.account is not None and chosen.account.account_id in account_map:
            await sticky_repo.upsert(sticky_key, chosen.account.account_id, kind=sticky_kind)
        return chosen

    async def mark_rate_limit(self, account: Account, error: UpstreamError) -> None:
        async with self._runtime_lock:
            state = self._state_for(account)
            handle_rate_limit(state, error)
            async with self._repo_factory() as repos:
                await self._sync_state(repos.accounts, account, state)

    async def mark_quota_exceeded(self, account: Account, error: UpstreamError) -> None:
        async with self._runtime_lock:
            state = self._state_for(account)
            handle_quota_exceeded(state, error)
            async with self._repo_factory() as repos:
                await self._sync_state(repos.accounts, account, state)

    async def mark_permanent_failure(self, account: Account, error_code: str) -> None:
        async with self._runtime_lock:
            state = self._state_for(account)
            handle_permanent_failure(state, error_code)
            async with self._repo_factory() as repos:
                await self._sync_state(repos.accounts, account, state)

    async def record_error(self, account: Account) -> None:
        async with self._runtime_lock:
            state = self._state_for(account)
            state.error_count += 1
            state.last_error_at = time.time()
            async with self._repo_factory() as repos:
                await self._sync_state(repos.accounts, account, state)

    async def record_success(self, account: Account) -> None:
        """Clear transient error state after a successful upstream request."""
        async with self._runtime_lock:
            runtime = self._runtime.get(account.id)
            if runtime and runtime.error_count > 0:
                runtime.error_count = 0
                runtime.last_error_at = None

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
    effective_secondary_entry = secondary_entry
    primary_row = _usage_entry_to_window_row(primary_entry) if primary_entry is not None else None
    secondary_row = _usage_entry_to_window_row(secondary_entry) if secondary_entry is not None else None
    # Weekly-only accounts may not emit a dedicated secondary row; treat the
    # weekly primary row as quota-window input for balancer decisions. When
    # both rows exist, prefer the newer weekly snapshot.
    if primary_row is not None and usage_core.should_use_weekly_primary(primary_row, secondary_row):
        effective_secondary_entry = primary_entry

    secondary_used = effective_secondary_entry.used_percent if effective_secondary_entry else None
    secondary_reset = effective_secondary_entry.reset_at if effective_secondary_entry else None

    # Use account.reset_at from DB as the authoritative source for runtime reset
    # and to survive process restarts.
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


def _gated_limit_name_for_model(model: str | None) -> str | None:
    return get_additional_quota_key_for_model_id(model)


def _mapped_model_has_registry_entry(model: str | None) -> bool:
    if model is None:
        return False
    registry = get_model_registry()
    get_snapshot = getattr(registry, "get_snapshot", None)
    if not callable(get_snapshot):
        return False
    snapshot = get_snapshot()
    if snapshot is None:
        return False
    return model.strip().lower() in snapshot.model_plans


def _usage_entry_to_window_row(entry: UsageHistory) -> UsageWindowRow:
    return UsageWindowRow(
        account_id=entry.account_id,
        used_percent=entry.used_percent,
        reset_at=entry.reset_at,
        window_minutes=entry.window_minutes,
        recorded_at=entry.recorded_at,
    )


def _clone_account(account: Account) -> Account:
    data = {column.name: getattr(account, column.name) for column in Account.__table__.columns}
    return Account(**data)


def _filter_accounts_for_tags(accounts: Iterable[Account], tags: Collection[str]) -> list[Account]:
    tag_set = {tag.strip().lower() for tag in tags if tag.strip()}
    if not tag_set:
        return list(accounts)
    return [
        account
        for account in accounts
        if any(link.tag_name in tag_set for link in account.tag_links)
    ]


def _clone_usage_history(entry: UsageHistory) -> UsageHistory:
    data = {column.name: getattr(entry, column.name) for column in UsageHistory.__table__.columns}
    return UsageHistory(**data)


async def _latest_additional_by_key(
    additional_usage_repo,
    quota_key: str,
    window: str,
    *,
    account_ids: list[str] | None = None,
    since: datetime | None = None,
) -> dict[str, AdditionalUsageHistory]:
    resolved_quota_key = canonicalize_additional_quota_key(
        quota_key=quota_key,
        limit_name=quota_key,
    )
    if resolved_quota_key is None:
        return {}
    if hasattr(additional_usage_repo, "latest_by_quota_key"):
        return await additional_usage_repo.latest_by_quota_key(
            resolved_quota_key,
            window,
            account_ids=account_ids,
            since=since,
        )
    return await additional_usage_repo.latest_by_account(
        resolved_quota_key,
        window,
        account_ids=account_ids,
        since=since,
    )


def _additional_usage_fresh_since(now: datetime | None = None) -> datetime:
    current_time = now or utcnow()
    interval_seconds = max(get_settings().usage_refresh_interval_seconds * 2, 180)
    return current_time - timedelta(seconds=interval_seconds)


def _additional_quota_eligibility(
    *,
    account_id: str,
    latest_primary: dict[str, AdditionalUsageHistory],
    latest_secondary: dict[str, AdditionalUsageHistory],
    fresh_primary: dict[str, AdditionalUsageHistory],
    fresh_secondary: dict[str, AdditionalUsageHistory],
) -> str:
    latest_primary_entry = latest_primary.get(account_id)
    latest_secondary_entry = latest_secondary.get(account_id)
    primary_entry = fresh_primary.get(account_id)
    secondary_entry = fresh_secondary.get(account_id)

    if latest_primary_entry is None and latest_secondary_entry is None:
        return "data_unavailable"
    if latest_primary_entry is not None and primary_entry is None:
        return "data_unavailable"
    if latest_secondary_entry is not None and secondary_entry is None:
        return "data_unavailable"

    if primary_entry is not None and _additional_usage_is_exhausted(primary_entry):
        return "quota_exhausted"
    if secondary_entry is not None and _additional_usage_is_exhausted(secondary_entry):
        return "quota_exhausted"
    return "eligible"


def _additional_usage_is_exhausted(entry: AdditionalUsageHistory) -> bool:
    if entry.used_percent is None:
        return False
    if entry.reset_at is not None and int(entry.reset_at) <= int(time.time()):
        return False
    return float(entry.used_percent) >= 100.0
