from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Collection
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Iterable

from sqlalchemy.exc import SQLAlchemyError

from app.core import usage as usage_core
from app.core.balancer import (
    HEALTH_TIER_DRAINING,
    HEALTH_TIER_HEALTHY,
    HEALTH_TIER_PROBING,
    QUOTA_EXCEEDED_COOLDOWN_SECONDS,
    ROUTING_POLICY_BURN_FIRST,
    ROUTING_POLICY_PRESERVE,
    TRAFFIC_CLASS_FOREGROUND,
    TRAFFIC_CLASS_OPPORTUNISTIC,
    AccountState,
    RoutingStrategy,
    SelectionResult,
    TrafficClass,
    evaluate_health_tier,
    handle_permanent_failure,
    handle_quota_exceeded,
    handle_rate_limit,
    select_account,
)
from app.core.balancer.types import UpstreamError
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.openai.model_registry import get_model_registry
from app.core.resilience.circuit_breaker import are_all_account_circuit_breakers_open
from app.core.resilience.degradation import get_status as get_degradation_status
from app.core.resilience.degradation import set_degraded, set_normal
from app.core.usage.quota import apply_usage_quota
from app.core.usage.types import UsageWindowRow
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, AdditionalUsageHistory, StickySessionKind, UsageHistory
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.proxy.additional_model_limits import get_additional_quota_key_for_model_id
from app.modules.proxy.repo_bundle import ProxyRepoFactory, ProxyRepositories
from app.modules.settings.service import parse_additional_quota_routing_policies
from app.modules.usage.additional_quota_keys import (
    canonicalize_additional_quota_key,
    get_additional_quota_routing_policy,
)

if TYPE_CHECKING:
    from app.modules.accounts.repository import AccountsRepository
    from app.modules.proxy.sticky_repository import StickySessionsRepository

logger = logging.getLogger(__name__)

_MAX_SELECTION_ATTEMPTS = 4

_STICKY_GRACE_PERIOD_SECONDS = 10.0
_RECOVERABLE_STATUSES = frozenset(
    {
        AccountStatus.ACTIVE,
        AccountStatus.RATE_LIMITED,
        AccountStatus.QUOTA_EXCEEDED,
    }
)

NO_PLAN_SUPPORT_FOR_MODEL = "no_plan_support_for_model"
ADDITIONAL_QUOTA_DATA_UNAVAILABLE = "additional_quota_data_unavailable"
NO_ADDITIONAL_QUOTA_ELIGIBLE_ACCOUNTS = "no_additional_quota_eligible_accounts"
OPPORTUNISTIC_BURN_WINDOW_CLOSED = "opportunistic_burn_window_closed"


@dataclass
class RuntimeState:
    reset_at: float | None = None
    cooldown_until: float | None = None
    last_error_at: float | None = None
    last_selected_at: float | None = None
    last_foreground_selected_at: float | None = None
    error_count: int = 0
    version: int = 0
    blocked_at: float | None = None
    health_tier: int = 0
    drain_entered_at: float | None = None
    probe_success_streak: int = 0


@dataclass
class AccountSelection:
    account: Account | None
    error_message: str | None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class _AdditionalLimitFilterResult:
    accounts: list[Account]
    latest_primary: dict[str, AdditionalUsageHistory]
    latest_secondary: dict[str, AdditionalUsageHistory]
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class _SelectionInputs:
    accounts: list[Account]
    latest_primary: dict[str, UsageHistory | AdditionalUsageHistory]
    latest_secondary: dict[str, UsageHistory | AdditionalUsageHistory]
    runtime_accounts: list[Account] | None = None
    error_message: str | None = None
    error_code: str | None = None
    ignore_standard_quota_status: bool = False
    routing_policy_override: str | None = None


SelectionInputs = _SelectionInputs


class LoadBalancer:
    def __init__(self, repo_factory: ProxyRepoFactory) -> None:
        self._repo_factory = repo_factory
        self._runtime: dict[str, RuntimeState] = {}
        self._runtime_lock = asyncio.Lock()
        self._account_locks: dict[str, asyncio.Lock] = {}
        self._account_locks_registry_lock = asyncio.Lock()
        self._selection_inputs_cache = get_account_selection_cache()

    async def select_account(
        self,
        sticky_key: str | None = None,
        *,
        sticky_kind: StickySessionKind | None = None,
        reallocate_sticky: bool = False,
        sticky_max_age_seconds: int | None = None,
        prefer_earlier_reset_accounts: bool = False,
        routing_strategy: RoutingStrategy = "capacity_weighted",
        model: str | None = None,
        additional_limit_name: str | None = None,
        account_ids: Collection[str] | None = None,
        exclude_account_ids: Collection[str] | None = None,
        budget_threshold_pct: float = 95.0,
        traffic_class: TrafficClass = TRAFFIC_CLASS_FOREGROUND,
    ) -> AccountSelection:
        excluded_ids = set(exclude_account_ids or ())
        scoped_account_ids = None if account_ids is None else set(account_ids)

        async def load_selection_inputs() -> _SelectionInputs:
            selection_inputs = await self._load_selection_inputs(
                model=model,
                additional_limit_name=additional_limit_name,
                account_ids=scoped_account_ids,
            )
            if excluded_ids and selection_inputs.accounts:
                selection_inputs = _SelectionInputs(
                    accounts=[account for account in selection_inputs.accounts if account.id not in excluded_ids],
                    latest_primary=selection_inputs.latest_primary,
                    latest_secondary=selection_inputs.latest_secondary,
                    runtime_accounts=selection_inputs.runtime_accounts,
                    error_message=selection_inputs.error_message,
                    error_code=selection_inputs.error_code,
                    ignore_standard_quota_status=selection_inputs.ignore_standard_quota_status,
                    routing_policy_override=selection_inputs.routing_policy_override,
                )
            return selection_inputs

        selection_inputs = await load_selection_inputs()
        circuit_breaker_open = _is_upstream_circuit_breaker_open()
        if circuit_breaker_open:
            set_degraded("upstream circuit breaker is open")
        elif selection_inputs.accounts:
            set_normal()
        elif selection_inputs.error_code is not None:
            set_normal()

        if selection_inputs.error_code is not None and not selection_inputs.accounts:
            return AccountSelection(
                account=None,
                error_message=selection_inputs.error_message,
                error_code=selection_inputs.error_code,
            )

        selected_snapshot: Account | None = None
        error_message: str | None = None
        selected_states: list[AccountState] = []
        selected_account_map: dict[str, Account] = {}
        if sticky_key is None:
            attempt = 0
            while True:
                attempt += 1
                self._prune_runtime(selection_inputs.runtime_accounts or selection_inputs.accounts)
                states, account_map = _build_states(
                    accounts=selection_inputs.accounts,
                    latest_primary=selection_inputs.latest_primary,
                    latest_secondary=selection_inputs.latest_secondary,
                    runtime=self._runtime,
                    routing_policy_override=selection_inputs.routing_policy_override,
                )

                result = _select_account_preferring_budget_safe(
                    states,
                    prefer_earlier_reset=prefer_earlier_reset_accounts,
                    routing_strategy=routing_strategy,
                    budget_threshold_pct=budget_threshold_pct,
                    traffic_class=traffic_class,
                    ignore_standard_quota=selection_inputs.ignore_standard_quota_status,
                )

                selected_account_map = account_map
                selected_states = []
                for state in states:
                    account = account_map.get(state.account_id)
                    if account is None:
                        continue
                    await self._sync_runtime_state_for_account(
                        account,
                        state,
                        selected=result.account is not None and state.account_id == result.account.account_id,
                        traffic_class=traffic_class,
                    )
                    selected_states.append(state)

                if result.account is not None:
                    selected = account_map.get(result.account.account_id)
                    if selected is None:
                        error_message = result.error_message
                    else:
                        selected_reset_at = selected.reset_at
                        for state in states:
                            if state.account_id == result.account.account_id:
                                state.status = result.account.status
                                state.deactivation_reason = result.account.deactivation_reason
                                selected_reset_at = int(state.reset_at) if state.reset_at else None
                                break
                        selected_snapshot = _clone_account(selected)
                        selected_snapshot.status = result.account.status
                        selected_snapshot.deactivation_reason = result.account.deactivation_reason
                        selected_snapshot.reset_at = selected_reset_at
                else:
                    error_message = result.error_message

                pre_persist_runtime_state = {
                    aid: (
                        runtime.reset_at,
                        runtime.cooldown_until,
                        runtime.error_count,
                        runtime.last_error_at,
                    )
                    for aid, runtime in self._runtime.items()
                }
                pre_persist_cache_generation = self._selection_inputs_cache.generation

                if selection_inputs.ignore_standard_quota_status:
                    stale_account_ids = set()
                else:
                    async with self._repo_factory() as repos:
                        stale_account_ids = await self._persist_selection_state(
                            repos.accounts,
                            selected_account_map,
                            selected_states,
                        )
                stale_account_ids = stale_account_ids or set()
                if selected_snapshot is not None and selected_snapshot.id in stale_account_ids:
                    if attempt >= _MAX_SELECTION_ATTEMPTS:
                        selected_snapshot = None
                        error_message = None
                        break
                    selection_inputs = await load_selection_inputs()
                    if selection_inputs.error_code is not None and not selection_inputs.accounts:
                        return AccountSelection(
                            account=None,
                            error_message=selection_inputs.error_message,
                            error_code=selection_inputs.error_code,
                        )
                    selected_snapshot = None
                    error_message = None
                    selected_states = []
                    selected_account_map = {}
                    continue

                if (
                    selected_snapshot is not None
                    and self._selection_inputs_cache.generation != pre_persist_cache_generation
                    and attempt < _MAX_SELECTION_ATTEMPTS
                ):
                    selection_inputs = await load_selection_inputs()
                    if selection_inputs.error_code is not None and not selection_inputs.accounts:
                        return AccountSelection(
                            account=None,
                            error_message=selection_inputs.error_message,
                            error_code=selection_inputs.error_code,
                        )
                    selected_snapshot = None
                    error_message = None
                    selected_states = []
                    selected_account_map = {}
                    await asyncio.sleep(0)
                    continue

                if selected_snapshot is None and error_message == "No available accounts":
                    runtime_recovered = any(
                        self._runtime.get(account_id, RuntimeState()).reset_at != before[0]
                        or self._runtime.get(account_id, RuntimeState()).cooldown_until != before[1]
                        or self._runtime.get(account_id, RuntimeState()).error_count != before[2]
                        or self._runtime.get(account_id, RuntimeState()).last_error_at != before[3]
                        for account_id, before in pre_persist_runtime_state.items()
                    )
                    if runtime_recovered and attempt < _MAX_SELECTION_ATTEMPTS:
                        selection_inputs = await load_selection_inputs()
                        if selection_inputs.error_code is not None and not selection_inputs.accounts:
                            return AccountSelection(
                                account=None,
                                error_message=selection_inputs.error_message,
                                error_code=selection_inputs.error_code,
                            )
                        error_message = None
                        selected_states = []
                        selected_account_map = {}
                        await asyncio.sleep(0)
                        continue

                break

        else:
            attempt = 0
            while True:
                attempt += 1
                self._prune_runtime(selection_inputs.runtime_accounts or selection_inputs.accounts)
                states, account_map = _build_states(
                    accounts=selection_inputs.accounts,
                    latest_primary=selection_inputs.latest_primary,
                    latest_secondary=selection_inputs.latest_secondary,
                    runtime=self._runtime,
                    routing_policy_override=selection_inputs.routing_policy_override,
                )
                async with self._repo_factory() as repos:
                    result = await self._select_with_stickiness(
                        states=states,
                        account_map=account_map,
                        sticky_key=sticky_key,
                        sticky_kind=sticky_kind,
                        reallocate_sticky=reallocate_sticky,
                        sticky_max_age_seconds=sticky_max_age_seconds,
                        budget_threshold_pct=budget_threshold_pct,
                        prefer_earlier_reset_accounts=prefer_earlier_reset_accounts,
                        routing_strategy=routing_strategy,
                        sticky_repo=repos.sticky_sessions,
                        traffic_class=traffic_class,
                        ignore_standard_quota=selection_inputs.ignore_standard_quota_status,
                    )
                    selected_account_map = account_map
                    selected_states = []
                    for state in states:
                        account = account_map.get(state.account_id)
                        if account is None:
                            continue
                        await self._sync_runtime_state_for_account(
                            account,
                            state,
                            selected=result.account is not None and state.account_id == result.account.account_id,
                            traffic_class=traffic_class,
                        )
                        selected_states.append(state)
                    if result.account is not None:
                        selected = account_map.get(result.account.account_id)
                        if selected is None:
                            error_message = result.error_message
                        else:
                            selected_reset_at = selected.reset_at
                            for state in selected_states:
                                if state.account_id == result.account.account_id:
                                    state.status = result.account.status
                                    state.deactivation_reason = result.account.deactivation_reason
                                    selected_reset_at = int(state.reset_at) if state.reset_at else None
                                    break
                            selected_snapshot = _clone_account(selected)
                            selected_snapshot.status = result.account.status
                            selected_snapshot.deactivation_reason = result.account.deactivation_reason
                            selected_snapshot.reset_at = selected_reset_at
                    else:
                        error_message = result.error_message

                    if selection_inputs.ignore_standard_quota_status:
                        stale_account_ids = set()
                    else:
                        stale_account_ids = await self._persist_selection_state(
                            repos.accounts,
                            selected_account_map,
                            selected_states,
                        )
                stale_account_ids = stale_account_ids or set()
                if selected_snapshot is not None and selected_snapshot.id in stale_account_ids:
                    selected_snapshot = None
                    error_message = None
                    selected_states = []
                    selected_account_map = {}
                    if attempt >= _MAX_SELECTION_ATTEMPTS:
                        break
                    selection_inputs = await load_selection_inputs()
                    if selection_inputs.error_code is not None and not selection_inputs.accounts:
                        return AccountSelection(
                            account=None,
                            error_message=selection_inputs.error_message,
                            error_code=selection_inputs.error_code,
                        )
                    await asyncio.sleep(0)
                    continue
                break

        if selected_snapshot is None:
            logger.warning(
                "No account selected strategy=%s sticky=%s model=%s error=%s",
                routing_strategy,
                bool(sticky_key),
                model,
                error_message,
            )

        if selected_snapshot is None:
            if traffic_class == TRAFFIC_CLASS_OPPORTUNISTIC and error_message:
                return AccountSelection(
                    account=None,
                    error_message=error_message,
                    error_code=OPPORTUNISTIC_BURN_WINDOW_CLOSED,
                )
            if error_message == "No available accounts":
                set_degraded("all upstream accounts are unavailable")
                error_message = _format_degraded_error_message(error_message)
            return AccountSelection(account=None, error_message=error_message, error_code=None)
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
        account_ids: Collection[str] | None = None,
    ) -> _SelectionInputs:
        cache_key = (
            model,
            additional_limit_name,
            None if account_ids is None else tuple(sorted(set(account_ids))),
        )
        cached = await self._selection_inputs_cache.get(cache_key)
        if cached is not None:
            return _clone_selection_inputs(cached)

        load_generation = self._selection_inputs_cache.generation

        async with self._repo_factory() as repos:
            all_accounts = await repos.accounts.list_accounts()
            effective_limit_name = additional_limit_name or _gated_limit_name_for_model(model)
            ignore_standard_quota_status = effective_limit_name is not None
            routing_policy_override: str | None = None
            if effective_limit_name:
                routing_overrides = await _load_additional_quota_routing_overrides()
                additional_routing_policy = get_additional_quota_routing_policy(
                    effective_limit_name,
                    overrides=routing_overrides,
                )
                if additional_routing_policy != "inherit":
                    routing_policy_override = additional_routing_policy
            accounts = _selectable_accounts(all_accounts)
            if account_ids is not None:
                allowed_account_ids = set(account_ids)
                accounts = [account for account in accounts if account.id in allowed_account_ids]
            pre_model_filter_accounts = accounts
            if model and (effective_limit_name is None or _mapped_model_has_registry_entry(model)):
                accounts = _filter_accounts_for_model(pre_model_filter_accounts, model)
            if model and not accounts:
                if not all_accounts:
                    selection_inputs = _SelectionInputs(
                        accounts=[],
                        latest_primary={},
                        latest_secondary={},
                        runtime_accounts=[_clone_account(account) for account in all_accounts],
                    )
                    await self._selection_inputs_cache.set(
                        _clone_selection_inputs(selection_inputs), key=cache_key, generation=load_generation
                    )
                    return selection_inputs
                if not pre_model_filter_accounts:
                    selection_inputs = _SelectionInputs(
                        accounts=[],
                        latest_primary={},
                        latest_secondary={},
                        runtime_accounts=[_clone_account(account) for account in all_accounts],
                    )
                    await self._selection_inputs_cache.set(
                        _clone_selection_inputs(selection_inputs), key=cache_key, generation=load_generation
                    )
                    return selection_inputs
                selection_inputs = _SelectionInputs(
                    accounts=[],
                    latest_primary={},
                    latest_secondary={},
                    runtime_accounts=[_clone_account(account) for account in all_accounts],
                    error_message=f"No accounts with a plan supporting model '{model}'",
                    error_code=NO_PLAN_SUPPORT_FOR_MODEL,
                )
                await self._selection_inputs_cache.set(
                    _clone_selection_inputs(selection_inputs), key=cache_key, generation=load_generation
                )
                return selection_inputs

            if effective_limit_name:
                additional_filter = await self._filter_accounts_for_additional_limit(
                    accounts,
                    model=model,
                    limit_name=effective_limit_name,
                    repos=repos,
                )
                accounts = additional_filter.accounts
                if not accounts:
                    selection_inputs = _SelectionInputs(
                        accounts=[],
                        latest_primary={},
                        latest_secondary={},
                        runtime_accounts=[_clone_account(account) for account in all_accounts],
                        error_message=additional_filter.error_message,
                        error_code=additional_filter.error_code,
                    )
                    await self._selection_inputs_cache.set(
                        _clone_selection_inputs(selection_inputs), key=cache_key, generation=load_generation
                    )
                    return selection_inputs
            if not accounts:
                selection_inputs = _SelectionInputs(
                    accounts=[],
                    latest_primary={},
                    latest_secondary={},
                    runtime_accounts=[_clone_account(account) for account in all_accounts],
                )
                await self._selection_inputs_cache.set(
                    _clone_selection_inputs(selection_inputs), key=cache_key, generation=load_generation
                )
                return selection_inputs

            if effective_limit_name:
                latest_primary = additional_filter.latest_primary
                latest_secondary = additional_filter.latest_secondary
            else:
                latest_primary, latest_secondary = await asyncio.gather(
                    repos.usage.latest_by_account(),
                    repos.usage.latest_by_account(window="secondary"),
                )
            selection_inputs = _SelectionInputs(
                accounts=[_clone_account(account) for account in accounts],
                latest_primary={
                    account_id: _clone_usage_history(entry) for account_id, entry in latest_primary.items()
                },
                latest_secondary={
                    account_id: _clone_usage_history(entry) for account_id, entry in latest_secondary.items()
                },
                runtime_accounts=[_clone_account(account) for account in all_accounts],
                ignore_standard_quota_status=ignore_standard_quota_status,
                routing_policy_override=routing_policy_override,
            )
            await self._selection_inputs_cache.set(
                _clone_selection_inputs(selection_inputs), key=cache_key, generation=load_generation
            )
            return selection_inputs

    async def check_opportunistic_admission(
        self,
        *,
        model: str | None,
        account_ids: Collection[str] | None,
        prefer_earlier_reset_accounts: bool,
        routing_strategy: RoutingStrategy,
        budget_threshold_pct: float,
    ) -> AccountSelection:
        selection_inputs = await self._load_selection_inputs(
            model=model,
            account_ids=account_ids,
        )
        if selection_inputs.error_code is not None and not selection_inputs.accounts:
            return AccountSelection(
                account=None,
                error_message=selection_inputs.error_message,
                error_code=selection_inputs.error_code,
            )
        states, account_map = _build_states(
            accounts=selection_inputs.accounts,
            latest_primary=selection_inputs.latest_primary,
            latest_secondary=selection_inputs.latest_secondary,
            runtime=self._runtime,
            routing_policy_override=selection_inputs.routing_policy_override,
        )
        result = _select_account_preferring_budget_safe(
            states,
            prefer_earlier_reset=prefer_earlier_reset_accounts,
            routing_strategy=routing_strategy,
            budget_threshold_pct=budget_threshold_pct,
            deterministic_probe=True,
            traffic_class=TRAFFIC_CLASS_OPPORTUNISTIC,
            ignore_standard_quota=selection_inputs.ignore_standard_quota_status,
        )
        if result.account is None:
            return AccountSelection(
                account=None,
                error_message=result.error_message,
                error_code=OPPORTUNISTIC_BURN_WINDOW_CLOSED,
            )
        account = account_map.get(result.account.account_id)
        if account is None:
            return AccountSelection(
                account=None,
                error_message=result.error_message or "opportunistic burn window closed: no account available",
                error_code=OPPORTUNISTIC_BURN_WINDOW_CLOSED,
            )
        return AccountSelection(account=_clone_account(account), error_message=None, error_code=None)

    async def _filter_accounts_for_additional_limit(
        self,
        accounts: list[Account],
        *,
        model: str | None,
        limit_name: str,
        repos: ProxyRepositories,
    ) -> _AdditionalLimitFilterResult:
        if not accounts:
            return _AdditionalLimitFilterResult(accounts=[], latest_primary={}, latest_secondary={})

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
            return _AdditionalLimitFilterResult(
                accounts=[],
                latest_primary=latest_primary,
                latest_secondary=latest_secondary,
                error_code=error_code,
                error_message=error_message,
            )

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
        eligible_ids = {account.id for account in eligible_accounts}
        return _AdditionalLimitFilterResult(
            accounts=eligible_accounts,
            latest_primary={
                account_id: entry for account_id, entry in latest_primary.items() if account_id in eligible_ids
            },
            latest_secondary={
                account_id: entry for account_id, entry in latest_secondary.items() if account_id in eligible_ids
            },
        )

    def _prune_runtime(self, accounts: Iterable[Account]) -> None:
        account_ids = {account.id for account in accounts}
        stale_ids = [account_id for account_id in self._runtime if account_id not in account_ids]
        for account_id in stale_ids:
            self._runtime.pop(account_id, None)

    async def _get_account_lock(self, account_id: str) -> asyncio.Lock:
        lock = self._account_locks.get(account_id)
        if lock is not None:
            return lock
        async with self._account_locks_registry_lock:
            lock = self._account_locks.get(account_id)
            if lock is None:
                lock = asyncio.Lock()
                self._account_locks[account_id] = lock
            return lock

    async def _sync_runtime_state_for_account(
        self,
        account: Account,
        state: AccountState,
        *,
        selected: bool = False,
        expected_version: int | None = None,
        traffic_class: TrafficClass = TRAFFIC_CLASS_FOREGROUND,
    ) -> bool:
        lock = await self._get_account_lock(account.id)
        async with lock:
            return self._sync_runtime_state(
                account,
                state,
                selected=selected,
                expected_version=expected_version,
                traffic_class=traffic_class,
            )

    async def _select_with_stickiness(
        self,
        *,
        states: list[AccountState],
        account_map: dict[str, Account],
        sticky_key: str | None,
        sticky_kind: StickySessionKind | None,
        reallocate_sticky: bool,
        sticky_max_age_seconds: int | None,
        budget_threshold_pct: float = 95.0,
        prefer_earlier_reset_accounts: bool,
        routing_strategy: RoutingStrategy,
        sticky_repo: StickySessionsRepository | None,
        traffic_class: TrafficClass = TRAFFIC_CLASS_FOREGROUND,
        ignore_standard_quota: bool = False,
    ) -> SelectionResult:
        if not sticky_key or not sticky_repo:
            return _select_account_preferring_budget_safe(
                states,
                prefer_earlier_reset=prefer_earlier_reset_accounts,
                routing_strategy=routing_strategy,
                budget_threshold_pct=budget_threshold_pct,
                traffic_class=traffic_class,
                ignore_standard_quota=ignore_standard_quota,
            )
        if sticky_kind is None:
            raise ValueError("sticky_kind is required when sticky_key is provided")

        existing = await sticky_repo.get_account_id(
            sticky_key,
            kind=sticky_kind,
            max_age_seconds=sticky_max_age_seconds,
        )

        # When the pinned account is temporarily unavailable (rate-limited,
        # error backoff) but still in the pool, pick a fallback WITHOUT
        # overwriting the sticky mapping so the next request returns to the
        # original account — and its warm OpenAI prompt cache — once it
        # recovers.  Only reallocate_sticky=True opts in to permanent
        # reassignment.
        persist_fallback = True

        if existing:
            pinned = next((state for state in states if state.account_id == existing), None)
            if pinned is not None:
                # Proactively rebind session affinity for prompt-cache and
                # codex sessions once the pinned account is already above the
                # configured budget threshold. That preserves continuity below
                # the threshold while avoiding obvious short-window failures
                # once the session is skating on the edge of exhaustion.
                now = time.time()
                budget_pressured = (
                    sticky_kind in (StickySessionKind.PROMPT_CACHE, StickySessionKind.CODEX_SESSION)
                    and pinned.status != AccountStatus.RATE_LIMITED
                    and _state_above_sticky_budget_threshold(pinned, budget_threshold_pct)
                )
                burn_first_reallocate = pinned.routing_policy != ROUTING_POLICY_BURN_FIRST and any(
                    state.routing_policy == ROUTING_POLICY_BURN_FIRST for state in states
                )
                preserve_reallocate = False
                if pinned.routing_policy == ROUTING_POLICY_PRESERVE:
                    non_preserve_candidates = [
                        state
                        for state in states
                        if state.account_id != pinned.account_id and state.routing_policy != ROUTING_POLICY_PRESERVE
                    ]
                    non_preserve_selection = _select_account_preferring_budget_safe(
                        non_preserve_candidates,
                        prefer_earlier_reset=prefer_earlier_reset_accounts,
                        routing_strategy=routing_strategy,
                        deterministic_probe=True,
                        budget_threshold_pct=budget_threshold_pct,
                        traffic_class=traffic_class,
                        ignore_standard_quota=ignore_standard_quota,
                    )
                    preserve_reallocate = non_preserve_selection.account is not None
                routing_policy_reallocate = burn_first_reallocate or preserve_reallocate
                rate_limit_far_away = (
                    sticky_kind == StickySessionKind.PROMPT_CACHE
                    and pinned.status == AccountStatus.RATE_LIMITED
                    and pinned.reset_at is not None
                    and pinned.reset_at - now >= 600  # 10 minutes
                )
                if not (budget_pressured or routing_policy_reallocate or rate_limit_far_away):
                    pinned_result = select_account(
                        [pinned],
                        prefer_earlier_reset=prefer_earlier_reset_accounts,
                        routing_strategy=routing_strategy,
                        allow_backoff_fallback=False,
                        traffic_class=traffic_class,
                        ignore_standard_quota=ignore_standard_quota,
                    )
                    if pinned_result.account is not None:
                        if sticky_max_age_seconds is not None:
                            await sticky_repo.upsert(sticky_key, pinned.account_id, kind=sticky_kind)
                        return pinned_result
                else:
                    # Before reallocating, check whether the pool has a
                    # meaningfully better candidate.  When every account
                    # is above the budget threshold, reallocating just
                    # wastes DB writes and destroys prompt-cache locality
                    # (thrashing).
                    if budget_pressured:
                        pool_best = _select_account_preferring_budget_safe(
                            states,
                            prefer_earlier_reset=prefer_earlier_reset_accounts,
                            routing_strategy=routing_strategy,
                            deterministic_probe=True,
                            budget_threshold_pct=budget_threshold_pct,
                            traffic_class=traffic_class,
                            ignore_standard_quota=ignore_standard_quota,
                        )
                        pool_exhausted = (
                            _state_above_budget_threshold
                            if _state_above_budget_threshold(pinned, budget_threshold_pct)
                            else _state_above_sticky_budget_threshold
                        )
                        pool_also_exhausted = pool_best.account is not None and (
                            pool_best.account.account_id == pinned.account_id
                            or pool_exhausted(pool_best.account, budget_threshold_pct)
                        )
                        if pool_also_exhausted:
                            pinned_result = select_account(
                                [pinned],
                                prefer_earlier_reset=prefer_earlier_reset_accounts,
                                routing_strategy=routing_strategy,
                                allow_backoff_fallback=False,
                                traffic_class=traffic_class,
                                ignore_standard_quota=ignore_standard_quota,
                            )
                            if pinned_result.account is not None:
                                if sticky_max_age_seconds is not None:
                                    await sticky_repo.upsert(
                                        sticky_key,
                                        pinned.account_id,
                                        kind=sticky_kind,
                                    )
                                return pinned_result
                    reallocate_sticky = True
                # Grace period: if the pinned account is rate-limited with a
                # known reset time within a short window, retry selection
                # with a small time advance to preserve prompt cache.
                # A shallow copy is used so the time-advanced selection does
                # not mutate the original state (which is later synced to DB
                # by _sync_state for all accounts).
                if not reallocate_sticky and pinned.status == AccountStatus.RATE_LIMITED:
                    grace_copy = replace(pinned)
                    grace_result = select_account(
                        [grace_copy],
                        now=time.time() + _STICKY_GRACE_PERIOD_SECONDS,
                        prefer_earlier_reset=prefer_earlier_reset_accounts,
                        routing_strategy=routing_strategy,
                        allow_backoff_fallback=False,
                        traffic_class=traffic_class,
                        ignore_standard_quota=ignore_standard_quota,
                    )
                    if grace_result.account is not None:
                        if sticky_max_age_seconds is not None:
                            await sticky_repo.upsert(sticky_key, pinned.account_id, kind=sticky_kind)
                        return grace_result
                if reallocate_sticky:
                    await sticky_repo.delete(sticky_key, kind=sticky_kind)
                elif pinned.status not in _RECOVERABLE_STATUSES:
                    # Permanently down (PAUSED/DEACTIVATED) — let the
                    # fallback be persisted to rebind the mapping.
                    pass
                elif sticky_max_age_seconds is not None:
                    # TTL-based kind (PROMPT_CACHE): preserve the original
                    # mapping so the next request returns to the warm-cache
                    # account once it recovers.  The TTL will naturally
                    # expire the mapping if recovery takes too long.
                    persist_fallback = False
                # else: durable kind without TTL (CODEX_SESSION) — persist
                # fallback so the session sticks to one account during
                # the outage instead of bouncing across random fallbacks.
            else:
                await sticky_repo.delete(sticky_key, kind=sticky_kind)

        chosen = _select_account_preferring_budget_safe(
            states,
            prefer_earlier_reset=prefer_earlier_reset_accounts,
            routing_strategy=routing_strategy,
            budget_threshold_pct=budget_threshold_pct,
            traffic_class=traffic_class,
            ignore_standard_quota=ignore_standard_quota,
        )
        if persist_fallback and chosen.account is not None and chosen.account.account_id in account_map:
            await sticky_repo.upsert(sticky_key, chosen.account.account_id, kind=sticky_kind)
        return chosen

    async def mark_rate_limit(self, account: Account, error: UpstreamError) -> None:
        lock = await self._get_account_lock(account.id)
        async with lock:
            state = self._state_for(account)
            handle_rate_limit(state, error)
            self._sync_runtime_state(account, state)
            async with self._repo_factory() as repos:
                await self._persist_state(repos.accounts, account, state)
            self._selection_inputs_cache.invalidate()

    async def mark_quota_exceeded(self, account: Account, error: UpstreamError) -> None:
        lock = await self._get_account_lock(account.id)
        async with lock:
            state = self._state_for(account)
            handle_quota_exceeded(state, error)
            self._sync_runtime_state(account, state)
            async with self._repo_factory() as repos:
                await self._persist_state(repos.accounts, account, state)
            self._selection_inputs_cache.invalidate()

    async def mark_permanent_failure(self, account: Account, error_code: str) -> None:
        lock = await self._get_account_lock(account.id)
        async with lock:
            state = self._state_for(account)
            handle_permanent_failure(state, error_code)
            self._sync_runtime_state(account, state)
            async with self._repo_factory() as repos:
                await self._persist_state(repos.accounts, account, state)
            self._selection_inputs_cache.invalidate()

    async def record_error(self, account: Account) -> None:
        await self.record_errors(account, 1)

    async def record_errors(self, account: Account, count: int) -> None:
        """Record *count* transient errors in a single lock acquisition."""
        if count < 1:
            return
        lock = await self._get_account_lock(account.id)
        async with lock:
            account_snapshot = _clone_account(account)
            state = self._state_for(account)
            state.error_count += count
            state.last_error_at = time.time()
            self._sync_runtime_state(account, state)
            runtime = self._runtime.get(account.id)
            if runtime and runtime.health_tier == HEALTH_TIER_PROBING:
                runtime.probe_success_streak = 0
            async with self._repo_factory() as repos:
                await self._persist_state_if_current(repos.accounts, account_snapshot, state)

    async def record_success(self, account: Account) -> None:
        """Clear transient error state after a successful upstream request."""
        lock = await self._get_account_lock(account.id)
        async with lock:
            runtime = self._runtime.get(account.id)
            if runtime and runtime.error_count > 0:
                runtime.error_count = 0
                runtime.last_error_at = None
                runtime.version += 1
            if runtime and runtime.health_tier == HEALTH_TIER_PROBING:
                runtime.probe_success_streak += 1
                runtime.version += 1

    def _state_for(self, account: Account) -> AccountState:
        runtime = self._runtime.setdefault(account.id, RuntimeState())
        return AccountState(
            account_id=account.id,
            status=account.status,
            used_percent=None,
            reset_at=runtime.reset_at,
            blocked_at=float(account.blocked_at) if account.blocked_at is not None else runtime.blocked_at,
            cooldown_until=runtime.cooldown_until,
            secondary_used_percent=None,
            secondary_reset_at=None,
            last_error_at=runtime.last_error_at,
            last_selected_at=runtime.last_selected_at,
            last_foreground_selected_at=runtime.last_foreground_selected_at,
            error_count=runtime.error_count,
            deactivation_reason=account.deactivation_reason,
            plan_type=account.plan_type,
            capacity_credits=usage_core.capacity_for_plan(account.plan_type, "secondary"),
            routing_policy=account.routing_policy,
        )

    def _sync_runtime_state(
        self,
        account: Account,
        state: AccountState,
        *,
        selected: bool = False,
        expected_version: int | None = None,
        traffic_class: TrafficClass = TRAFFIC_CLASS_FOREGROUND,
    ) -> bool:
        runtime = self._runtime.setdefault(account.id, RuntimeState())
        if expected_version is not None and runtime.version != expected_version:
            if selected:
                runtime.last_selected_at = time.time()
                if traffic_class == TRAFFIC_CLASS_FOREGROUND:
                    runtime.last_foreground_selected_at = runtime.last_selected_at
                runtime.version += 1
            return False

        dirty = False
        if runtime.reset_at != state.reset_at:
            runtime.reset_at = state.reset_at
            dirty = True
        if runtime.cooldown_until != state.cooldown_until:
            runtime.cooldown_until = state.cooldown_until
            dirty = True
        if runtime.blocked_at != state.blocked_at:
            runtime.blocked_at = state.blocked_at
            dirty = True
        if runtime.last_error_at != state.last_error_at:
            runtime.last_error_at = state.last_error_at
            dirty = True
        if runtime.error_count != state.error_count:
            runtime.error_count = state.error_count
            dirty = True
        if account.status != state.status:
            dirty = True
        if account.deactivation_reason != state.deactivation_reason:
            dirty = True
        if selected:
            runtime.last_selected_at = time.time()
            if traffic_class == TRAFFIC_CLASS_FOREGROUND:
                runtime.last_foreground_selected_at = runtime.last_selected_at
            dirty = True
        if dirty:
            runtime.version += 1
        return True

    async def _persist_selection_state(
        self,
        accounts_repo: AccountsRepository,
        account_map: dict[str, Account],
        states: list[AccountState],
    ) -> set[str]:
        stale_account_ids: set[str] = set()
        for state in states:
            account = account_map.get(state.account_id)
            if account is not None:
                persisted = await self._persist_state_if_current(accounts_repo, account, state)
                if not persisted:
                    stale_account_ids.add(account.id)
        return stale_account_ids

    async def _persist_state(
        self,
        accounts_repo: AccountsRepository,
        account: Account,
        state: AccountState,
    ) -> None:
        reset_at_int = int(state.reset_at) if state.reset_at else None
        blocked_at_int = int(state.blocked_at) if state.blocked_at else None
        status_changed = account.status != state.status
        reason_changed = account.deactivation_reason != state.deactivation_reason
        reset_changed = account.reset_at != reset_at_int
        blocked_changed = account.blocked_at != blocked_at_int

        if status_changed or reason_changed or reset_changed or blocked_changed:
            await accounts_repo.update_status(
                account.id,
                state.status,
                state.deactivation_reason,
                reset_at_int,
                blocked_at=blocked_at_int,
            )
            account.status = state.status
            account.deactivation_reason = state.deactivation_reason
            account.reset_at = reset_at_int
            account.blocked_at = blocked_at_int

    async def _persist_state_if_current(
        self,
        accounts_repo: AccountsRepository,
        account: Account,
        state: AccountState,
    ) -> bool:
        reset_at_int = int(state.reset_at) if state.reset_at else None
        blocked_at_int = int(state.blocked_at) if state.blocked_at else None
        status_changed = account.status != state.status
        reason_changed = account.deactivation_reason != state.deactivation_reason
        reset_changed = account.reset_at != reset_at_int
        blocked_changed = account.blocked_at != blocked_at_int

        if status_changed or reason_changed or reset_changed or blocked_changed:
            updated = await accounts_repo.update_status_if_current(
                account.id,
                state.status,
                state.deactivation_reason,
                reset_at_int,
                blocked_at=blocked_at_int,
                expected_status=account.status,
                expected_deactivation_reason=account.deactivation_reason,
                expected_reset_at=account.reset_at,
                expected_blocked_at=account.blocked_at,
            )
            if updated:
                account.status = state.status
                account.deactivation_reason = state.deactivation_reason
                account.reset_at = reset_at_int
                account.blocked_at = blocked_at_int
            return updated
        return True

    async def _sync_state(
        self,
        accounts_repo: AccountsRepository,
        account: Account,
        state: AccountState,
    ) -> None:
        self._sync_runtime_state(account, state)
        await self._persist_state(accounts_repo, account, state)


def _build_states(
    *,
    accounts: Iterable[Account],
    latest_primary: dict[str, UsageHistory | AdditionalUsageHistory],
    latest_secondary: dict[str, UsageHistory | AdditionalUsageHistory],
    runtime: dict[str, RuntimeState],
    routing_policy_override: str | None = None,
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
        if routing_policy_override is not None:
            state.routing_policy = routing_policy_override
        states.append(state)
        account_map[account.id] = account
    return states, account_map


def _state_from_account(
    *,
    account: Account,
    primary_entry: UsageHistory | AdditionalUsageHistory | None,
    secondary_entry: UsageHistory | AdditionalUsageHistory | None,
    runtime: RuntimeState,
) -> AccountState:
    primary_used = primary_entry.used_percent if primary_entry else None
    primary_reset = primary_entry.reset_at if primary_entry else None
    primary_window_minutes = primary_entry.window_minutes if primary_entry else None
    primary_usage_fresh = (
        primary_entry is not None
        and primary_entry.recorded_at is not None
        and _usage_entry_is_recent_enough(primary_entry.recorded_at)
    )
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
    secondary_usage_fresh = (
        effective_secondary_entry is not None
        and effective_secondary_entry.recorded_at is not None
        and _usage_entry_is_recent_enough(effective_secondary_entry.recorded_at)
    )

    # Use account.reset_at from DB as the authoritative source for runtime reset
    # and to survive process restarts.
    db_reset_at = float(account.reset_at) if account.reset_at else None
    effective_runtime_reset = db_reset_at or runtime.reset_at
    effective_blocked_at = float(account.blocked_at) if account.blocked_at is not None else runtime.blocked_at

    if (
        account.status == AccountStatus.QUOTA_EXCEEDED
        and effective_runtime_reset is not None
        and effective_runtime_reset > time.time()
        and effective_blocked_at is None
        and effective_secondary_entry is not None
        and _usage_entry_is_recent_enough(effective_secondary_entry.recorded_at)
        and effective_secondary_entry.used_percent is not None
        and float(effective_secondary_entry.used_percent) < 100.0
        and effective_secondary_entry.reset_at is not None
        and float(effective_secondary_entry.reset_at) > effective_runtime_reset
    ):
        effective_runtime_reset = None

    # Clear the runtime reset guard only when a post-block refresh has been
    # observed and the debounce period is over.
    #
    # QUOTA_EXCEEDED uses a persisted blocked_at marker so recovery survives
    # process restarts. RATE_LIMITED keeps the narrower runtime-only behavior,
    # because its cooldown duration is not persisted today.
    cooldown_ready = False
    if account.status == AccountStatus.QUOTA_EXCEEDED:
        cooldown_ready = (
            effective_blocked_at is not None and time.time() >= effective_blocked_at + QUOTA_EXCEEDED_COOLDOWN_SECONDS
        )
    elif (
        runtime.cooldown_until is not None and runtime.cooldown_until <= time.time() and runtime.blocked_at is not None
    ):
        cooldown_ready = True

    if cooldown_ready and effective_blocked_at is not None:
        if account.status == AccountStatus.QUOTA_EXCEEDED:
            freshness_entry = effective_secondary_entry
        elif account.status == AccountStatus.RATE_LIMITED:
            freshness_entry = primary_entry
        else:
            freshness_entry = None
        if freshness_entry and freshness_entry.recorded_at is not None:
            recorded_epoch = freshness_entry.recorded_at.replace(tzinfo=timezone.utc).timestamp()
            if recorded_epoch > effective_blocked_at:
                effective_runtime_reset = None

    status, used_percent, reset_at = apply_usage_quota(
        status=account.status,
        primary_used=primary_used,
        primary_reset=primary_reset,
        primary_window_minutes=primary_window_minutes,
        runtime_reset=effective_runtime_reset,
        secondary_used=secondary_used,
        secondary_reset=secondary_reset,
    )

    next_blocked_at = (
        effective_blocked_at if status in (AccountStatus.QUOTA_EXCEEDED, AccountStatus.RATE_LIMITED) else None
    )

    settings = get_settings()
    if getattr(settings, "soft_drain_enabled", True):
        new_tier = evaluate_health_tier(
            AccountState(
                account_id=account.id,
                status=status,
                used_percent=used_percent,
                secondary_used_percent=secondary_used,
                primary_usage_fresh=primary_usage_fresh,
                secondary_usage_fresh=secondary_usage_fresh,
                last_error_at=runtime.last_error_at,
                error_count=runtime.error_count,
                health_tier=runtime.health_tier,
                routing_policy=account.routing_policy,
            ),
            now=time.time(),
            drain_entered_at=runtime.drain_entered_at,
            probe_success_streak=runtime.probe_success_streak,
            drain_primary_threshold_pct=getattr(settings, "drain_primary_threshold_pct", 85.0),
            drain_secondary_threshold_pct=getattr(settings, "drain_secondary_threshold_pct", 90.0),
            drain_error_window_seconds=getattr(settings, "drain_error_window_seconds", 60.0),
            drain_error_count_threshold=getattr(settings, "drain_error_count_threshold", 2),
            probe_quiet_seconds=getattr(settings, "probe_quiet_seconds", 60.0),
            probe_success_streak_required=getattr(settings, "probe_success_streak_required", 3),
        )
        if new_tier == HEALTH_TIER_DRAINING and runtime.health_tier != HEALTH_TIER_DRAINING:
            runtime.drain_entered_at = time.time()
            runtime.probe_success_streak = 0
        if new_tier == HEALTH_TIER_HEALTHY:
            runtime.drain_entered_at = None
            runtime.probe_success_streak = 0
        runtime.health_tier = new_tier
    else:
        new_tier = HEALTH_TIER_HEALTHY
        runtime.drain_entered_at = None
        runtime.probe_success_streak = 0
        runtime.health_tier = HEALTH_TIER_HEALTHY

    return AccountState(
        account_id=account.id,
        status=status,
        used_percent=used_percent,
        reset_at=reset_at,
        primary_reset_at=primary_reset,
        blocked_at=next_blocked_at,
        cooldown_until=runtime.cooldown_until,
        secondary_used_percent=secondary_used,
        secondary_reset_at=secondary_reset,
        primary_usage_fresh=primary_usage_fresh,
        secondary_usage_fresh=secondary_usage_fresh,
        last_error_at=runtime.last_error_at,
        last_selected_at=runtime.last_selected_at,
        last_foreground_selected_at=runtime.last_foreground_selected_at,
        error_count=runtime.error_count,
        deactivation_reason=account.deactivation_reason,
        plan_type=account.plan_type,
        capacity_credits=usage_core.capacity_for_plan(account.plan_type, "secondary"),
        health_tier=new_tier,
        routing_policy=account.routing_policy,
    )


def _usage_entry_is_recent_enough(recorded_at: datetime | None) -> bool:
    if recorded_at is None:
        return False
    current_time = utcnow()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    usage_refresh_interval = getattr(get_settings(), "usage_refresh_interval_seconds", 60)
    interval_seconds = max(usage_refresh_interval * 2, 180)
    recorded_time = recorded_at if recorded_at.tzinfo is not None else recorded_at.replace(tzinfo=timezone.utc)
    return recorded_time >= current_time - timedelta(seconds=interval_seconds)


async def _load_additional_quota_routing_overrides() -> dict[str, str]:
    try:
        dashboard_settings = await get_settings_cache().get()
    except SQLAlchemyError:
        logger.warning("Additional quota routing policy settings unavailable; using inherited policies", exc_info=True)
        return {}
    return parse_additional_quota_routing_policies(dashboard_settings.additional_quota_routing_policies_json)


def _filter_accounts_for_model(accounts: list[Account], model: str) -> list[Account]:
    allowed_plans = get_model_registry().plan_types_for_model(model)
    if allowed_plans is None:
        return accounts
    return [a for a in accounts if a.plan_type in allowed_plans]


def _selectable_accounts(accounts: list[Account]) -> list[Account]:
    return [account for account in accounts if account.status not in (AccountStatus.DEACTIVATED, AccountStatus.PAUSED)]


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
    model_plans = getattr(snapshot, "model_plans", None)
    if not isinstance(model_plans, dict):
        return False
    return model.strip().lower() in model_plans


def _usage_entry_to_window_row(entry: UsageHistory | AdditionalUsageHistory) -> UsageWindowRow:
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


def _clone_usage_history(entry: UsageHistory | AdditionalUsageHistory) -> UsageHistory | AdditionalUsageHistory:
    if isinstance(entry, AdditionalUsageHistory):
        data = {column.name: getattr(entry, column.name) for column in AdditionalUsageHistory.__table__.columns}
        return AdditionalUsageHistory(**data)
    data = {column.name: getattr(entry, column.name) for column in UsageHistory.__table__.columns}
    return UsageHistory(**data)


def _clone_selection_inputs(selection_inputs: SelectionInputs) -> SelectionInputs:
    return _SelectionInputs(
        accounts=[_clone_account(account) for account in selection_inputs.accounts],
        latest_primary={
            account_id: _clone_usage_history(entry) for account_id, entry in selection_inputs.latest_primary.items()
        },
        latest_secondary={
            account_id: _clone_usage_history(entry) for account_id, entry in selection_inputs.latest_secondary.items()
        },
        runtime_accounts=(
            None
            if selection_inputs.runtime_accounts is None
            else [_clone_account(account) for account in selection_inputs.runtime_accounts]
        ),
        error_message=selection_inputs.error_message,
        error_code=selection_inputs.error_code,
        ignore_standard_quota_status=selection_inputs.ignore_standard_quota_status,
        routing_policy_override=selection_inputs.routing_policy_override,
    )


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
    from app.core.config.settings import get_settings  # noqa: PLC0415

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


def _state_above_budget_threshold(state: AccountState, budget_threshold_pct: float) -> bool:
    return state.used_percent is not None and state.used_percent > budget_threshold_pct


def _state_above_sticky_budget_threshold(state: AccountState, budget_threshold_pct: float) -> bool:
    return (state.used_percent is not None and state.used_percent > budget_threshold_pct) or (
        state.secondary_used_percent is not None and state.secondary_used_percent > budget_threshold_pct
    )


def _select_account_preferring_budget_safe(
    states: Iterable[AccountState],
    *,
    prefer_earlier_reset: bool,
    routing_strategy: RoutingStrategy,
    budget_threshold_pct: float,
    allow_backoff_fallback: bool = True,
    deterministic_probe: bool = False,
    traffic_class: TrafficClass = TRAFFIC_CLASS_FOREGROUND,
    ignore_standard_quota: bool = False,
) -> SelectionResult:
    state_list = list(states)
    burn_first_states = [state for state in state_list if state.routing_policy == ROUTING_POLICY_BURN_FIRST]
    if burn_first_states:
        burn_first = select_account(
            state_list if traffic_class == TRAFFIC_CLASS_OPPORTUNISTIC else burn_first_states,
            prefer_earlier_reset=prefer_earlier_reset,
            routing_strategy=routing_strategy,
            allow_backoff_fallback=False,
            deterministic_probe=deterministic_probe,
            traffic_class=traffic_class,
            ignore_standard_quota=ignore_standard_quota,
        )
        if burn_first.account is not None:
            return burn_first

    preferred_states = [
        state
        for state in state_list
        if state.routing_policy != ROUTING_POLICY_PRESERVE
        and not _state_above_budget_threshold(state, budget_threshold_pct)
    ]
    if preferred_states:
        selection_pool = preferred_states if len(preferred_states) != len(state_list) else state_list
        preferred = select_account(
            selection_pool,
            prefer_earlier_reset=prefer_earlier_reset,
            routing_strategy=routing_strategy,
            allow_backoff_fallback=allow_backoff_fallback,
            deterministic_probe=deterministic_probe,
            traffic_class=traffic_class,
            ignore_standard_quota=ignore_standard_quota,
        )
        if preferred.account is not None:
            return preferred
        if len(preferred_states) == len(state_list):
            return preferred
    if routing_strategy == "usage_weighted" and state_list:
        return select_account(
            state_list,
            prefer_earlier_reset=prefer_earlier_reset,
            routing_strategy=routing_strategy,
            allow_backoff_fallback=allow_backoff_fallback,
            deterministic_probe=deterministic_probe,
            usage_weighted_order="primary_first",
            traffic_class=traffic_class,
            ignore_standard_quota=ignore_standard_quota,
        )
    return select_account(
        state_list,
        prefer_earlier_reset=prefer_earlier_reset,
        routing_strategy=routing_strategy,
        allow_backoff_fallback=allow_backoff_fallback,
        deterministic_probe=deterministic_probe,
        traffic_class=traffic_class,
        ignore_standard_quota=ignore_standard_quota,
    )


def _is_upstream_circuit_breaker_open() -> bool:
    settings = get_settings()
    if not getattr(settings, "circuit_breaker_enabled", False):
        return False
    return are_all_account_circuit_breakers_open()


def _format_degraded_error_message(message: str | None) -> str:
    degradation_status = get_degradation_status()
    reason = degradation_status.get("reason") or "upstream capacity is currently unavailable"
    base_message = message or "Upstream unavailable"
    return f"{base_message}. Service is operating in degraded mode: {reason}"
