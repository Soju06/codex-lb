from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Iterable, Literal
from uuid import uuid4

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
    ResetPreferenceWindow,
    RoutingCostsByAccount,
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
from app.core.config import settings as config_settings
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    account_cap_rejections_total,
    account_lease_acquired_total,
    account_lease_released_total,
    account_lease_stale_reclaimed_total,
)
from app.core.openai.model_registry import get_model_registry
from app.core.plan_types import account_plan_matches_allowed, normalize_account_plan_type
from app.core.resilience.circuit_breaker import are_all_account_circuit_breakers_open
from app.core.resilience.degradation import get_status as get_degradation_status
from app.core.resilience.degradation import set_degraded, set_normal
from app.core.usage.quota import apply_usage_quota
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, AdditionalUsageHistory, StickySessionKind, UsageHistory
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.proxy.additional_model_limits import get_additional_quota_key_for_model_id
from app.modules.proxy.repo_bundle import ProxyRepoFactory, ProxyRepositories
from app.modules.quota_planner.logic import PlannerSettings, build_routing_costs
from app.modules.usage.additional_quota_keys import (
    canonicalize_additional_quota_key,
    get_additional_quota_definition,
    get_additional_quota_routing_policy,
)
from app.modules.usage.mappers import usage_history_to_window_row

if TYPE_CHECKING:
    from app.modules.accounts.repository import AccountsRepository
    from app.modules.proxy.sticky_repository import StickySessionsRepository

logger = logging.getLogger(__name__)

_UsageWindowEntry = UsageHistory | AdditionalUsageHistory

_MAX_SELECTION_ATTEMPTS = 4

_ACCOUNT_STREAM_LEASE_STALE_GRACE_SECONDS = 60.0
_STICKY_GRACE_PERIOD_SECONDS = 10.0
_STICKY_EXISTING_UNSET = object()
_RECOVERABLE_STATUSES = frozenset(
    {
        AccountStatus.ACTIVE,
        AccountStatus.RATE_LIMITED,
        AccountStatus.QUOTA_EXCEEDED,
    }
)

_DEFAULT_USAGE_REFRESH_INTERVAL_SECONDS = 60

NO_PLAN_SUPPORT_FOR_MODEL = "no_plan_support_for_model"
ADDITIONAL_QUOTA_DATA_UNAVAILABLE = "additional_quota_data_unavailable"
ADDITIONAL_QUOTA_EXHAUSTED = "quota_exhausted"
NO_ADDITIONAL_QUOTA_ELIGIBLE_ACCOUNTS = "no_additional_quota_eligible_accounts"
_ADDITIONAL_QUOTA_EXEMPT_PLAN_TYPES = frozenset({"free", "plus", "edu"})
_ROUTING_POLICY_NORMAL = "normal"
_ACCOUNT_ROUTING_POLICIES = frozenset({_ROUTING_POLICY_NORMAL, ROUTING_POLICY_BURN_FIRST, ROUTING_POLICY_PRESERVE})
_ADDITIONAL_QUOTA_ROUTING_POLICIES = _ACCOUNT_ROUTING_POLICIES | frozenset({"inherit"})
OPPORTUNISTIC_BURN_WINDOW_CLOSED = "opportunistic_burn_window_closed"

AccountLeaseKind = Literal["response_create", "stream"]


@dataclass
class RuntimeState:
    reset_at: float | None = None
    cooldown_until: float | None = None
    last_error_at: float | None = None
    last_selected_at: float | None = None
    error_count: int = 0
    version: int = 0
    blocked_at: float | None = None
    health_tier: int = 0
    drain_entered_at: float | None = None
    probe_success_streak: int = 0
    inflight_response_creates: int = 0
    inflight_streams: int = 0
    leased_tokens: float = 0.0
    leases: dict[str, "AccountLease"] | None = None


@dataclass(frozen=True, slots=True)
class AccountLease:
    lease_id: str
    account_id: str
    kind: AccountLeaseKind
    acquired_at: float
    estimated_tokens: float = 0.0


@dataclass
class AccountSelection:
    account: Account | None
    error_message: str | None
    error_code: str | None = None
    lease: AccountLease | None = None


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
    latest_monthly: dict[str, UsageHistory]
    quota_planner_settings: PlannerSettings = PlannerSettings()
    runtime_accounts: list[Account] | None = None
    error_message: str | None = None
    error_code: str | None = None
    ignore_standard_quota_account_ids: frozenset[str] = frozenset()
    ignore_standard_quota_status: bool = False
    persist_standard_quota_status: bool = True
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

    async def release_account_lease(self, lease: AccountLease | None) -> None:
        if lease is None:
            return
        async with self._runtime_lock:
            self._release_account_lease_locked(lease, reason="explicit")

    async def acquire_account_lease(
        self,
        account_id: str,
        *,
        kind: AccountLeaseKind,
        estimated_tokens: float = 0.0,
    ) -> AccountLease | None:
        async with self._runtime_lock:
            self._reclaim_stale_account_leases_locked()
            runtime = self._runtime.setdefault(account_id, RuntimeState())
            if kind == "response_create":
                cap = get_settings().proxy_account_response_create_limit
                if cap > 0 and runtime.inflight_response_creates >= cap:
                    _record_account_cap_rejection("response_create")
                    return None
            else:
                cap = get_settings().proxy_account_stream_limit
                if cap > 0 and runtime.inflight_streams >= cap:
                    _record_account_cap_rejection("stream")
                    return None
            return self._acquire_account_lease_locked(
                account_id,
                kind=kind,
                estimated_tokens=estimated_tokens,
            )

    async def account_pressure_snapshot(self, account_id: str) -> tuple[int, int, float]:
        async with self._runtime_lock:
            runtime = self._runtime.get(account_id)
            if runtime is None:
                return 0, 0, 0.0
            return runtime.inflight_response_creates, runtime.inflight_streams, runtime.leased_tokens

    def _acquire_account_lease_locked(
        self,
        account_id: str,
        *,
        kind: AccountLeaseKind,
        estimated_tokens: float,
    ) -> AccountLease:
        runtime = self._runtime.setdefault(account_id, RuntimeState())
        lease = AccountLease(
            lease_id=uuid4().hex,
            account_id=account_id,
            kind=kind,
            acquired_at=time.monotonic(),
            estimated_tokens=max(0.0, estimated_tokens),
        )
        if runtime.leases is None:
            runtime.leases = {}
        runtime.leases[lease.lease_id] = lease
        if kind == "response_create":
            runtime.inflight_response_creates += 1
        else:
            runtime.inflight_streams += 1
        runtime.leased_tokens += lease.estimated_tokens
        runtime.last_selected_at = time.time()
        runtime.version += 1
        _record_account_lease_acquired(kind)
        return lease

    def _account_lease_allowed_locked(self, account_id: str, *, kind: AccountLeaseKind) -> bool:
        runtime = self._runtime.setdefault(account_id, RuntimeState())
        if kind == "response_create":
            cap = get_settings().proxy_account_response_create_limit
            return cap <= 0 or runtime.inflight_response_creates < cap
        cap = get_settings().proxy_account_stream_limit
        return cap <= 0 or runtime.inflight_streams < cap

    def _release_account_lease_locked(self, lease: AccountLease, *, reason: str) -> bool:
        runtime = self._runtime.get(lease.account_id)
        if runtime is None or runtime.leases is None:
            return False
        current = runtime.leases.pop(lease.lease_id, None)
        if current is None:
            return False
        if current.kind == "response_create":
            runtime.inflight_response_creates = max(0, runtime.inflight_response_creates - 1)
        else:
            runtime.inflight_streams = max(0, runtime.inflight_streams - 1)
        runtime.leased_tokens = max(0.0, runtime.leased_tokens - current.estimated_tokens)
        runtime.version += 1
        _record_account_lease_released(current.kind, reason)
        if reason == "stale":
            _record_account_lease_stale_reclaimed(current.kind)
            logger.warning(
                "Reclaimed stale account lease account_id=%s kind=%s age_seconds=%.3f",
                current.account_id,
                current.kind,
                time.monotonic() - current.acquired_at,
            )
        return True

    def _reclaim_stale_account_leases_locked(self) -> None:
        settings = get_settings()
        now = time.monotonic()
        for runtime in self._runtime.values():
            if not runtime.leases:
                continue
            stale = [
                lease
                for lease in runtime.leases.values()
                if now - lease.acquired_at >= _account_lease_stale_ttl_seconds(lease.kind, settings)
            ]
            for lease in stale:
                self._release_account_lease_locked(lease, reason="stale")

    async def select_account(
        self,
        sticky_key: str | None = None,
        *,
        sticky_kind: StickySessionKind | None = None,
        reallocate_sticky: bool = False,
        sticky_max_age_seconds: int | None = None,
        prefer_earlier_reset_accounts: bool = False,
        prefer_earlier_reset_window: ResetPreferenceWindow = "secondary",
        routing_strategy: RoutingStrategy = "capacity_weighted",
        relative_availability_power: float = 2.0,
        relative_availability_top_k: int = 5,
        model: str | None = None,
        additional_limit_name: str | None = None,
        account_ids: Collection[str] | None = None,
        exclude_account_ids: Collection[str] | None = None,
        require_security_work_authorized: bool = False,
        budget_threshold_pct: float = 95.0,
        secondary_budget_threshold_pct: float = 100.0,
        routing_costs_by_account_id: RoutingCostsByAccount | None = None,
        lease_kind: AccountLeaseKind | None = None,
        estimated_lease_tokens: float = 0.0,
        traffic_class: TrafficClass = TRAFFIC_CLASS_FOREGROUND,
        ordered_account_ids: Collection[str] | None = None,
    ) -> AccountSelection:
        excluded_ids = set(exclude_account_ids or ())
        scoped_account_ids = None if account_ids is None else set(account_ids)

        async def load_selection_inputs() -> _SelectionInputs:
            selection_inputs = await self._load_selection_inputs(
                model=model,
                additional_limit_name=additional_limit_name,
                account_ids=scoped_account_ids,
            )
            if require_security_work_authorized and selection_inputs.accounts:
                authorized_accounts = [
                    account for account in selection_inputs.accounts if bool(account.security_work_authorized)
                ]
                if not authorized_accounts:
                    return _SelectionInputs(
                        accounts=[],
                        latest_primary={},
                        latest_secondary={},
                        latest_monthly=selection_inputs.latest_monthly,
                        quota_planner_settings=selection_inputs.quota_planner_settings,
                        runtime_accounts=selection_inputs.runtime_accounts,
                        error_message="No accounts marked as authorized for security work",
                        error_code="no_security_work_authorized_accounts",
                    )
                selection_inputs = _SelectionInputs(
                    accounts=authorized_accounts,
                    latest_primary=selection_inputs.latest_primary,
                    latest_secondary=selection_inputs.latest_secondary,
                    latest_monthly=selection_inputs.latest_monthly,
                    quota_planner_settings=selection_inputs.quota_planner_settings,
                    runtime_accounts=selection_inputs.runtime_accounts,
                    error_message=selection_inputs.error_message,
                    error_code=selection_inputs.error_code,
                    ignore_standard_quota_account_ids=selection_inputs.ignore_standard_quota_account_ids,
                    ignore_standard_quota_status=selection_inputs.ignore_standard_quota_status,
                    persist_standard_quota_status=selection_inputs.persist_standard_quota_status,
                    routing_policy_override=selection_inputs.routing_policy_override,
                )
            if excluded_ids and selection_inputs.accounts:
                filtered_accounts = [account for account in selection_inputs.accounts if account.id not in excluded_ids]
                if require_security_work_authorized and not filtered_accounts:
                    return _SelectionInputs(
                        accounts=[],
                        latest_primary={},
                        latest_secondary={},
                        latest_monthly=selection_inputs.latest_monthly,
                        quota_planner_settings=selection_inputs.quota_planner_settings,
                        runtime_accounts=selection_inputs.runtime_accounts,
                        error_message="No accounts marked as authorized for security work",
                        error_code="no_security_work_authorized_accounts",
                    )
                selection_inputs = _SelectionInputs(
                    accounts=filtered_accounts,
                    latest_primary=selection_inputs.latest_primary,
                    latest_secondary=selection_inputs.latest_secondary,
                    latest_monthly=selection_inputs.latest_monthly,
                    quota_planner_settings=selection_inputs.quota_planner_settings,
                    runtime_accounts=selection_inputs.runtime_accounts,
                    error_message=selection_inputs.error_message,
                    error_code=selection_inputs.error_code,
                    ignore_standard_quota_account_ids=selection_inputs.ignore_standard_quota_account_ids,
                    ignore_standard_quota_status=selection_inputs.ignore_standard_quota_status,
                    persist_standard_quota_status=selection_inputs.persist_standard_quota_status,
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
        selected_lease: AccountLease | None = None
        selection_error_code: str | None = None
        if sticky_key is None:
            attempt = 0
            while True:
                attempt += 1
                async with self._runtime_lock:
                    self._reclaim_stale_account_leases_locked()
                    self._prune_runtime(selection_inputs.runtime_accounts or selection_inputs.accounts)
                    states, account_map = _build_states(
                        accounts=selection_inputs.accounts,
                        latest_primary=selection_inputs.latest_primary,
                        latest_secondary=selection_inputs.latest_secondary,
                        latest_monthly=selection_inputs.latest_monthly,
                        runtime=self._runtime,
                        routing_policy_override=selection_inputs.routing_policy_override,
                        ignore_standard_quota_account_ids=selection_inputs.ignore_standard_quota_account_ids,
                    )
                    effective_routing_costs = (
                        routing_costs_by_account_id
                        if routing_costs_by_account_id is not None
                        else build_routing_costs(
                            settings=selection_inputs.quota_planner_settings,
                            states=states,
                            now=datetime.now(timezone.utc),
                        )
                    )
                    selection_states = _filter_states_for_account_caps(states, lease_kind=lease_kind)
                    if not selection_states and states:
                        result = SelectionResult(None, "No available accounts")
                        error_message = result.error_message
                        selection_error_code = _account_cap_error_code(lease_kind)
                        logger.warning(
                            "Account cap exhausted during selection lease_kind=%s reason=%s candidates=%s",
                            lease_kind,
                            selection_error_code,
                            len(states),
                        )
                        _record_account_cap_rejection(lease_kind)
                    else:
                        selection_error_code = None
                        result = _select_account_preferring_budget_safe(
                            selection_states,
                            prefer_earlier_reset=prefer_earlier_reset_accounts,
                            prefer_earlier_reset_window=prefer_earlier_reset_window,
                            routing_strategy=routing_strategy,
                            relative_availability_power=relative_availability_power,
                            relative_availability_top_k=relative_availability_top_k,
                            budget_threshold_pct=budget_threshold_pct,
                            secondary_budget_threshold_pct=secondary_budget_threshold_pct,
                            traffic_class=traffic_class,
                            ignore_standard_quota=False,
                            routing_costs_by_account_id=effective_routing_costs,
                            ordered_account_ids=ordered_account_ids,
                        )

                    selected_account_map = account_map
                    selected_states = []
                    for state in states:
                        account = account_map.get(state.account_id)
                        if account is None:
                            continue
                        self._sync_runtime_state(
                            account,
                            state,
                            selected=result.account is not None and state.account_id == result.account.account_id,
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
                            if lease_kind is not None:
                                selected_lease = self._acquire_account_lease_locked(
                                    selected.id,
                                    kind=lease_kind,
                                    estimated_tokens=estimated_lease_tokens,
                                )
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

                try:
                    async with self._repo_factory() as repos:
                        stale_account_ids = await self._persist_selection_state(
                            repos.accounts,
                            selected_account_map,
                            selected_states,
                        )
                except BaseException:
                    await self.release_account_lease(selected_lease)
                    selected_lease = None
                    raise
                stale_account_ids = stale_account_ids or set()
                if selected_snapshot is not None and selected_snapshot.id in stale_account_ids:
                    await self.release_account_lease(selected_lease)
                    selected_lease = None
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
                    await self.release_account_lease(selected_lease)
                    selected_lease = None
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
            sticky_existing_account_id: str | None | object = _STICKY_EXISTING_UNSET
            attempt = 0
            while True:
                attempt += 1
                async with self._runtime_lock:
                    self._reclaim_stale_account_leases_locked()
                    self._prune_runtime(selection_inputs.runtime_accounts or selection_inputs.accounts)
                    states, account_map = _build_states(
                        accounts=selection_inputs.accounts,
                        latest_primary=selection_inputs.latest_primary,
                        latest_secondary=selection_inputs.latest_secondary,
                        latest_monthly=selection_inputs.latest_monthly,
                        runtime=self._runtime,
                        routing_policy_override=selection_inputs.routing_policy_override,
                        ignore_standard_quota_account_ids=selection_inputs.ignore_standard_quota_account_ids,
                    )
                    effective_routing_costs = (
                        routing_costs_by_account_id
                        if routing_costs_by_account_id is not None
                        else build_routing_costs(
                            settings=selection_inputs.quota_planner_settings,
                            states=states,
                            now=datetime.now(timezone.utc),
                        )
                    )
                if sticky_key and sticky_kind == StickySessionKind.CODEX_SESSION:
                    async with self._repo_factory() as repos:
                        sticky_existing_account_id = await repos.sticky_sessions.get_account_id(
                            sticky_key,
                            kind=sticky_kind,
                            max_age_seconds=sticky_max_age_seconds,
                        )
                hard_sticky = sticky_kind == StickySessionKind.CODEX_SESSION and isinstance(
                    sticky_existing_account_id, str
                )
                selection_states = (
                    states if hard_sticky else _filter_states_for_account_caps(states, lease_kind=lease_kind)
                )
                if not selection_states and states:
                    result = SelectionResult(None, "No available accounts")
                    selection_error_code = _account_cap_error_code(lease_kind)
                    logger.warning(
                        "Account cap exhausted during sticky selection lease_kind=%s reason=%s candidates=%s",
                        lease_kind,
                        selection_error_code,
                        len(states),
                    )
                    _record_account_cap_rejection(lease_kind)
                else:
                    selection_error_code = None
                    async with self._repo_factory() as repos:
                        result = await self._select_with_stickiness(
                            states=selection_states,
                            account_map=account_map,
                            sticky_key=sticky_key,
                            sticky_kind=sticky_kind,
                            reallocate_sticky=reallocate_sticky,
                            sticky_max_age_seconds=sticky_max_age_seconds,
                            budget_threshold_pct=budget_threshold_pct,
                            secondary_budget_threshold_pct=secondary_budget_threshold_pct,
                            prefer_earlier_reset_accounts=prefer_earlier_reset_accounts,
                            prefer_earlier_reset_window=prefer_earlier_reset_window,
                            routing_strategy=routing_strategy,
                            relative_availability_power=relative_availability_power,
                            relative_availability_top_k=relative_availability_top_k,
                            sticky_repo=repos.sticky_sessions,
                            sticky_existing_account_id=sticky_existing_account_id,
                            traffic_class=traffic_class,
                            ignore_standard_quota=False,
                            routing_costs_by_account_id=effective_routing_costs,
                        )
                selected_account_map = account_map
                selected_states = []
                async with self._runtime_lock:
                    for state in states:
                        account = account_map.get(state.account_id)
                        if account is None:
                            continue
                        self._sync_runtime_state(
                            account,
                            state,
                            selected=result.account is not None and state.account_id == result.account.account_id,
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
                            if lease_kind is not None:
                                if not self._account_lease_allowed_locked(selected.id, kind=lease_kind):
                                    selected_snapshot = None
                                    error_message = "No available accounts"
                                    selection_error_code = _account_cap_error_code(lease_kind)
                                else:
                                    selected_lease = self._acquire_account_lease_locked(
                                        selected.id,
                                        kind=lease_kind,
                                        estimated_tokens=estimated_lease_tokens,
                                    )
                    else:
                        error_message = result.error_message

                try:
                    async with self._repo_factory() as repos:
                        stale_account_ids = await self._persist_selection_state(
                            repos.accounts,
                            selected_account_map,
                            selected_states,
                        )
                except BaseException:
                    await self.release_account_lease(selected_lease)
                    selected_lease = None
                    raise
                stale_account_ids = stale_account_ids or set()
                if selected_snapshot is not None and selected_snapshot.id in stale_account_ids:
                    await self.release_account_lease(selected_lease)
                    selected_lease = None
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
                if (
                    selected_snapshot is None
                    and selection_error_code is not None
                    and not hard_sticky
                    and attempt < _MAX_SELECTION_ATTEMPTS
                ):
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
            return AccountSelection(account=None, error_message=error_message, error_code=selection_error_code)
        logger.info(
            "Selected account_id=%s strategy=%s sticky=%s model=%s",
            selected_snapshot.id,
            routing_strategy,
            bool(sticky_key),
            model,
        )
        return AccountSelection(account=selected_snapshot, error_message=None, error_code=None, lease=selected_lease)

    async def _load_selection_inputs(
        self,
        *,
        model: str | None,
        additional_limit_name: str | None = None,
        account_ids: Collection[str] | None = None,
    ) -> _SelectionInputs:
        effective_limit_name = additional_limit_name or _gated_limit_name_for_model(model)
        additional_quota_routing_policies: dict[str, str] = {}
        if effective_limit_name is not None:
            additional_quota_routing_policies = await _load_dashboard_additional_quota_routing_overrides()
        additional_quota_routing_policies_cache_key = json.dumps(
            additional_quota_routing_policies,
            sort_keys=True,
            separators=(",", ":"),
        )
        cache_key = (
            model,
            additional_limit_name,
            additional_quota_routing_policies_cache_key,
            None if account_ids is None else tuple(sorted(set(account_ids))),
        )
        cached = await self._selection_inputs_cache.get(cache_key)
        if cached is not None:
            return _clone_selection_inputs(cached)

        load_generation = self._selection_inputs_cache.generation

        async with self._repo_factory() as repos:
            all_accounts = await repos.accounts.list_accounts()
            quota_planner_repo = getattr(repos, "quota_planner", None)
            get_quota_planner_settings = getattr(quota_planner_repo, "get_settings", None)
            if callable(get_quota_planner_settings):
                try:
                    settings_result = get_quota_planner_settings()
                    quota_planner_settings = (
                        await settings_result if inspect.isawaitable(settings_result) else settings_result
                    )
                    if not isinstance(quota_planner_settings, PlannerSettings):
                        quota_planner_settings = PlannerSettings()
                except Exception:
                    logger.warning("Failed to load quota planner settings; using defaults", exc_info=True)
                    quota_planner_settings = PlannerSettings()
            else:
                quota_planner_settings = PlannerSettings()
            ignore_standard_quota_status = effective_limit_name is not None
            routing_policy_override = _additional_quota_routing_policy_override(
                effective_limit_name,
                additional_quota_routing_policies,
            )
            accounts = _selectable_accounts(all_accounts)
            if account_ids is not None:
                allowed_account_ids = set(account_ids)
                accounts = [account for account in accounts if account.id in allowed_account_ids]
            pre_model_filter_accounts = accounts
            if model and _mapped_model_has_registry_entry(model):
                accounts = _filter_accounts_for_model(pre_model_filter_accounts, model)
            if model and not accounts:
                if not all_accounts:
                    selection_inputs = _SelectionInputs(
                        accounts=[],
                        latest_primary={},
                        latest_secondary={},
                        latest_monthly={},
                        quota_planner_settings=quota_planner_settings,
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
                        latest_monthly={},
                        quota_planner_settings=quota_planner_settings,
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
                    latest_monthly={},
                    quota_planner_settings=quota_planner_settings,
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
                    explicit_limit=additional_limit_name is not None,
                    repos=repos,
                )
                accounts = additional_filter.accounts
                if not accounts:
                    selection_inputs = _SelectionInputs(
                        accounts=[],
                        latest_primary={},
                        latest_secondary={},
                        latest_monthly={},
                        quota_planner_settings=quota_planner_settings,
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
                    latest_monthly={},
                    quota_planner_settings=quota_planner_settings,
                    runtime_accounts=[_clone_account(account) for account in all_accounts],
                )
                await self._selection_inputs_cache.set(
                    _clone_selection_inputs(selection_inputs), key=cache_key, generation=load_generation
                )
                return selection_inputs

            standard_latest_primary, standard_latest_secondary, latest_monthly = await asyncio.gather(
                repos.usage.latest_by_account(),
                repos.usage.latest_by_account(window="secondary"),
                repos.usage.latest_by_account(window="monthly"),
            )
            if effective_limit_name:
                model_allowed_plans = get_model_registry().plan_types_for_model(model) if model else None
                latest_primary = additional_filter.latest_primary
                latest_secondary = additional_filter.latest_secondary
                quota_scoped_account_ids = frozenset(
                    account.id
                    for account in accounts
                    if additional_limit_name is not None
                    or (
                        model_allowed_plans is not None
                        and normalize_account_plan_type(account.plan_type) not in _ADDITIONAL_QUOTA_EXEMPT_PLAN_TYPES
                        and account_plan_matches_allowed(
                            account.plan_type,
                            model_allowed_plans,
                        )
                    )
                )
                latest_primary: dict[str, UsageHistory | AdditionalUsageHistory] = dict(standard_latest_primary)
                latest_secondary: dict[str, UsageHistory | AdditionalUsageHistory] = dict(standard_latest_secondary)
                for account_id in quota_scoped_account_ids:
                    latest_primary.pop(account_id, None)
                    latest_secondary.pop(account_id, None)
                    if account_id in additional_filter.latest_primary:
                        latest_primary[account_id] = additional_filter.latest_primary[account_id]
                    if account_id in additional_filter.latest_secondary:
                        latest_secondary[account_id] = additional_filter.latest_secondary[account_id]
                ignore_standard_quota_account_ids = quota_scoped_account_ids
            else:
                latest_primary = standard_latest_primary
                latest_secondary = standard_latest_secondary
                ignore_standard_quota_account_ids = frozenset()
            selection_inputs = _SelectionInputs(
                accounts=[_clone_account(account) for account in accounts],
                latest_primary={
                    account_id: _clone_usage_history(entry) for account_id, entry in latest_primary.items()
                },
                latest_secondary={
                    account_id: _clone_usage_history(entry) for account_id, entry in latest_secondary.items()
                },
                latest_monthly={
                    account_id: _clone_standard_usage_history(entry) for account_id, entry in latest_monthly.items()
                },
                quota_planner_settings=quota_planner_settings,
                runtime_accounts=[_clone_account(account) for account in all_accounts],
                ignore_standard_quota_account_ids=ignore_standard_quota_account_ids,
                ignore_standard_quota_status=ignore_standard_quota_status,
                persist_standard_quota_status=True,
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
        prefer_earlier_reset_window: ResetPreferenceWindow = "secondary",
        secondary_budget_threshold_pct: float = 100.0,
        lease_kind: AccountLeaseKind | None = None,
        ordered_account_ids: Collection[str] | None = None,
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
        async with self._runtime_lock:
            self._reclaim_stale_account_leases_locked()
            self._prune_runtime(selection_inputs.runtime_accounts or selection_inputs.accounts)
            states, account_map = _build_states(
                accounts=selection_inputs.accounts,
                latest_primary=selection_inputs.latest_primary,
                latest_secondary=selection_inputs.latest_secondary,
                latest_monthly=selection_inputs.latest_monthly,
                runtime=self._runtime,
                routing_policy_override=selection_inputs.routing_policy_override,
                ignore_standard_quota_account_ids=selection_inputs.ignore_standard_quota_account_ids,
            )
            selection_states = _filter_states_for_account_caps(states, lease_kind=lease_kind)
            if not selection_states and states:
                logger.warning(
                    "Account cap exhausted during opportunistic admission lease_kind=%s reason=%s candidates=%s",
                    lease_kind,
                    _account_cap_error_code(lease_kind),
                    len(states),
                )
                _record_account_cap_rejection(lease_kind)
                return AccountSelection(
                    account=None,
                    error_message="opportunistic burn window closed: no account capacity available",
                    error_code=OPPORTUNISTIC_BURN_WINDOW_CLOSED,
                )
        result = _select_account_preferring_budget_safe(
            selection_states,
            prefer_earlier_reset=prefer_earlier_reset_accounts,
            prefer_earlier_reset_window=prefer_earlier_reset_window,
            routing_strategy=routing_strategy,
            budget_threshold_pct=budget_threshold_pct,
            secondary_budget_threshold_pct=secondary_budget_threshold_pct,
            apply_secondary_budget_threshold=True,
            deterministic_probe=True,
            traffic_class=TRAFFIC_CLASS_OPPORTUNISTIC,
            ignore_standard_quota=False,
            ordered_account_ids=ordered_account_ids,
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
        explicit_limit: bool = False,
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
        blocked_by_exhaustion = False
        for account in accounts:
            eligibility = _additional_quota_eligibility(
                account_id=account.id,
                account_plan_type=account.plan_type,
                quota_key=limit_name,
                explicit_limit=explicit_limit,
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
            elif eligibility == "quota_exhausted":
                blocked_by_exhaustion = True

        if not eligible_accounts:
            if blocked_by_data:
                error_code = ADDITIONAL_QUOTA_DATA_UNAVAILABLE
                error_message = f"No fresh additional quota data available for model '{model}'"
            elif blocked_by_exhaustion:
                error_code = ADDITIONAL_QUOTA_EXHAUSTED
                error_message = f"Additional quota exhausted for model '{model}'"
            else:
                error_code = NO_ADDITIONAL_QUOTA_ELIGIBLE_ACCOUNTS
                error_message = f"No accounts with available additional quota for model '{model}'"
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
        stale_ids = [
            account_id
            for account_id, runtime in self._runtime.items()
            if account_id not in account_ids and not runtime.leases
        ]
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
    ) -> bool:
        lock = await self._get_account_lock(account.id)
        async with lock:
            return self._sync_runtime_state(
                account,
                state,
                selected=selected,
                expected_version=expected_version,
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
        secondary_budget_threshold_pct: float = 100.0,
        prefer_earlier_reset_accounts: bool,
        prefer_earlier_reset_window: ResetPreferenceWindow,
        routing_strategy: RoutingStrategy,
        relative_availability_power: float = 2.0,
        relative_availability_top_k: int = 5,
        sticky_repo: StickySessionsRepository | None,
        routing_costs_by_account_id: RoutingCostsByAccount | None = None,
        sticky_existing_account_id: str | None | object = _STICKY_EXISTING_UNSET,
        traffic_class: TrafficClass = TRAFFIC_CLASS_FOREGROUND,
        ignore_standard_quota: bool = False,
    ) -> SelectionResult:
        if not sticky_key or not sticky_repo:
            return _select_account_preferring_budget_safe(
                states,
                prefer_earlier_reset=prefer_earlier_reset_accounts,
                prefer_earlier_reset_window=prefer_earlier_reset_window,
                routing_strategy=routing_strategy,
                relative_availability_power=relative_availability_power,
                relative_availability_top_k=relative_availability_top_k,
                budget_threshold_pct=budget_threshold_pct,
                traffic_class=traffic_class,
                ignore_standard_quota=ignore_standard_quota,
                routing_costs_by_account_id=routing_costs_by_account_id,
            )
        if sticky_kind is None:
            raise ValueError("sticky_kind is required when sticky_key is provided")

        if sticky_existing_account_id is _STICKY_EXISTING_UNSET:
            existing = await sticky_repo.get_account_id(
                sticky_key,
                kind=sticky_kind,
                max_age_seconds=sticky_max_age_seconds,
            )
        else:
            existing = sticky_existing_account_id if isinstance(sticky_existing_account_id, str) else None
        # When the pinned account is temporarily unavailable (rate-limited,
        # error backoff) but still in the pool, pick a fallback WITHOUT
        # overwriting the sticky mapping so the next request returns to the
        # original account — and its warm OpenAI prompt cache — once it
        # recovers.  Only reallocate_sticky=True opts in to permanent
        # reassignment.
        persist_fallback = True
        apply_sticky_secondary_budget_threshold = False

        if existing:
            pinned = next((state for state in states if state.account_id == existing), None)
            if pinned is not None:
                # Proactively rebind session affinity for any sticky kind
                # once the pinned account is already above the configured
                # budget threshold. That preserves continuity below the
                # threshold while avoiding obvious short-window failures once
                # the session is skating on the edge of exhaustion.
                now = time.time()
                budget_pressured = (
                    sticky_kind
                    in (
                        StickySessionKind.PROMPT_CACHE,
                        StickySessionKind.STICKY_THREAD,
                        StickySessionKind.CODEX_SESSION,
                    )
                    and routing_strategy not in ("sequential_drain", "reset_drain", "single_account")
                    and pinned.status != AccountStatus.RATE_LIMITED
                    and _state_above_sticky_budget_threshold(
                        pinned,
                        budget_threshold_pct,
                        secondary_budget_threshold_pct,
                    )
                )
                rate_limit_far_away = (
                    sticky_kind == StickySessionKind.PROMPT_CACHE
                    and pinned.status == AccountStatus.RATE_LIMITED
                    and pinned.reset_at is not None
                    and pinned.reset_at - now >= 600  # 10 minutes
                )

                burn_first_reallocate = pinned.routing_policy != ROUTING_POLICY_BURN_FIRST
                if burn_first_reallocate:
                    burn_first_candidates = [
                        state for state in states if state.routing_policy == ROUTING_POLICY_BURN_FIRST
                    ]
                    if burn_first_candidates:
                        burn_first = select_account(
                            burn_first_candidates,
                            prefer_earlier_reset=prefer_earlier_reset_accounts,
                            routing_strategy=routing_strategy,
                            allow_backoff_fallback=False,
                            deterministic_probe=True,
                            relative_availability_power=relative_availability_power,
                            relative_availability_top_k=relative_availability_top_k,
                            traffic_class=traffic_class,
                            ignore_standard_quota=ignore_standard_quota,
                        )
                        burn_first_reallocate = burn_first.account is not None

                if not ((budget_pressured or rate_limit_far_away) and burn_first_reallocate):
                    pinned_result = select_account(
                        [pinned],
                        prefer_earlier_reset=prefer_earlier_reset_accounts,
                        prefer_earlier_reset_window=prefer_earlier_reset_window,
                        routing_strategy=routing_strategy,
                        allow_backoff_fallback=False,
                        relative_availability_power=relative_availability_power,
                        relative_availability_top_k=relative_availability_top_k,
                        traffic_class=traffic_class,
                        ignore_standard_quota=ignore_standard_quota,
                        routing_costs=routing_costs_by_account_id,
                    )
                    if pinned_result.account is not None:
                        if sticky_max_age_seconds is not None:
                            await sticky_repo.upsert(sticky_key, pinned.account_id, kind=sticky_kind)
                        return pinned_result
                else:
                    # Reallocate only when a burn-first target exists and can
                    # currently be selected, avoiding sticky churn to
                    # ineligible targets.
                    # Before reallocating, check whether the pool has a
                    # meaningfully better candidate.  When every account
                    # is above the budget threshold, reallocating just
                    # wastes DB writes and destroys prompt-cache locality
                    # (thrashing).
                    if budget_pressured:
                        apply_sticky_secondary_budget_threshold = True
                        pool_best = _select_account_preferring_budget_safe(
                            states,
                            prefer_earlier_reset=prefer_earlier_reset_accounts,
                            prefer_earlier_reset_window=prefer_earlier_reset_window,
                            routing_strategy=routing_strategy,
                            relative_availability_power=relative_availability_power,
                            relative_availability_top_k=relative_availability_top_k,
                            deterministic_probe=True,
                            budget_threshold_pct=budget_threshold_pct,
                            secondary_budget_threshold_pct=secondary_budget_threshold_pct,
                            apply_secondary_budget_threshold=True,
                            traffic_class=traffic_class,
                            ignore_standard_quota=ignore_standard_quota,
                            routing_costs_by_account_id=routing_costs_by_account_id,
                        )
                        pool_also_exhausted = pool_best.account is not None and (
                            pool_best.account.account_id == pinned.account_id
                            or _state_above_sticky_budget_threshold(
                                pool_best.account,
                                budget_threshold_pct,
                                secondary_budget_threshold_pct,
                            )
                        )
                        if pool_also_exhausted:
                            pinned_result = select_account(
                                [pinned],
                                prefer_earlier_reset=prefer_earlier_reset_accounts,
                                prefer_earlier_reset_window=prefer_earlier_reset_window,
                                routing_strategy=routing_strategy,
                                allow_backoff_fallback=False,
                                relative_availability_power=relative_availability_power,
                                relative_availability_top_k=relative_availability_top_k,
                                traffic_class=traffic_class,
                                ignore_standard_quota=ignore_standard_quota,
                                routing_costs=routing_costs_by_account_id,
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
                        prefer_earlier_reset_window=prefer_earlier_reset_window,
                        routing_strategy=routing_strategy,
                        allow_backoff_fallback=False,
                        relative_availability_power=relative_availability_power,
                        relative_availability_top_k=relative_availability_top_k,
                        traffic_class=traffic_class,
                        ignore_standard_quota=ignore_standard_quota,
                        routing_costs=routing_costs_by_account_id,
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
            prefer_earlier_reset_window=prefer_earlier_reset_window,
            routing_strategy=routing_strategy,
            relative_availability_power=relative_availability_power,
            relative_availability_top_k=relative_availability_top_k,
            budget_threshold_pct=budget_threshold_pct,
            secondary_budget_threshold_pct=secondary_budget_threshold_pct,
            apply_secondary_budget_threshold=apply_sticky_secondary_budget_threshold,
            traffic_class=traffic_class,
            ignore_standard_quota=ignore_standard_quota,
            routing_costs_by_account_id=routing_costs_by_account_id,
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
        routing_policy = _normalize_account_routing_policy(getattr(account, "routing_policy", None))
        return AccountState(
            account_id=account.id,
            status=account.status,
            used_percent=None,
            reset_at=runtime.reset_at,
            primary_reset_at=None,
            blocked_at=float(account.blocked_at) if account.blocked_at is not None else runtime.blocked_at,
            cooldown_until=runtime.cooldown_until,
            secondary_used_percent=None,
            secondary_reset_at=None,
            last_error_at=runtime.last_error_at,
            last_selected_at=runtime.last_selected_at,
            error_count=runtime.error_count,
            deactivation_reason=account.deactivation_reason,
            plan_type=account.plan_type,
            capacity_credits=usage_core.capacity_for_plan(account.plan_type, "secondary"),
            routing_policy=routing_policy,
            ignore_standard_quota=False,
        )

    def _sync_runtime_state(
        self,
        account: Account,
        state: AccountState,
        *,
        selected: bool = False,
        expected_version: int | None = None,
    ) -> bool:
        runtime = self._runtime.setdefault(account.id, RuntimeState())
        if expected_version is not None and runtime.version != expected_version:
            if selected:
                runtime.last_selected_at = time.time()
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
            if state.ignore_standard_quota:
                continue
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
    latest_primary: Mapping[str, UsageHistory | AdditionalUsageHistory],
    latest_secondary: Mapping[str, UsageHistory | AdditionalUsageHistory],
    latest_monthly: Mapping[str, UsageHistory],
    runtime: dict[str, RuntimeState],
    routing_policy_override: str | None = None,
    ignore_standard_quota_account_ids: frozenset[str] = frozenset(),
) -> tuple[list[AccountState], dict[str, Account]]:
    states: list[AccountState] = []
    account_map: dict[str, Account] = {}

    for account in accounts:
        secondary_entry: UsageHistory | AdditionalUsageHistory | None = latest_secondary.get(account.id)
        if account.id not in ignore_standard_quota_account_ids:
            secondary_entry = _select_long_window_entry(
                account=account,
                monthly_entry=latest_monthly.get(account.id),
                secondary_entry=secondary_entry,
            )
        state = _state_from_account(
            account=account,
            primary_entry=latest_primary.get(account.id),
            secondary_entry=secondary_entry,
            runtime=runtime.setdefault(account.id, RuntimeState()),
        )
        if routing_policy_override is not None and account.id in ignore_standard_quota_account_ids:
            state.routing_policy = routing_policy_override
        state.ignore_standard_quota = account.id in ignore_standard_quota_account_ids
        states.append(state)
        account_map[account.id] = account
    return states, account_map


def _account_lease_stale_ttl_seconds(kind: AccountLeaseKind, settings: object) -> float:
    ttl_seconds = float(getattr(settings, "proxy_account_lease_ttl_seconds", 900.0))
    if kind != "stream":
        return ttl_seconds
    valid_stream_budget_seconds = max(
        ttl_seconds,
        float(getattr(settings, "proxy_request_budget_seconds", ttl_seconds)),
        float(getattr(settings, "http_responses_stream_request_budget_seconds", ttl_seconds)),
        float(getattr(settings, "http_responses_session_bridge_request_budget_seconds", ttl_seconds)),
    )
    return max(ttl_seconds, valid_stream_budget_seconds + _ACCOUNT_STREAM_LEASE_STALE_GRACE_SECONDS)


def _filter_states_for_account_caps(
    states: Iterable[AccountState],
    *,
    lease_kind: AccountLeaseKind | None,
) -> list[AccountState]:
    if lease_kind is None:
        return list(states)
    settings = get_settings()
    filtered: list[AccountState] = []
    for state in states:
        if lease_kind == "response_create":
            cap = settings.proxy_account_response_create_limit
            if cap > 0 and state.inflight_response_creates >= cap:
                continue
        else:
            cap = settings.proxy_account_stream_limit
            if cap > 0 and state.inflight_streams >= cap:
                continue
        filtered.append(state)
    return filtered


def _account_cap_error_code(lease_kind: AccountLeaseKind | None) -> str | None:
    if lease_kind == "response_create":
        return "account_response_create_cap"
    if lease_kind == "stream":
        return "account_stream_cap"
    return None


def _record_account_lease_acquired(kind: AccountLeaseKind) -> None:
    if PROMETHEUS_AVAILABLE and account_lease_acquired_total is not None:
        account_lease_acquired_total.labels(kind=kind).inc()


def _record_account_lease_released(kind: AccountLeaseKind, reason: str) -> None:
    if PROMETHEUS_AVAILABLE and account_lease_released_total is not None:
        account_lease_released_total.labels(kind=kind, reason=reason).inc()


def _record_account_lease_stale_reclaimed(kind: AccountLeaseKind) -> None:
    if PROMETHEUS_AVAILABLE and account_lease_stale_reclaimed_total is not None:
        account_lease_stale_reclaimed_total.labels(kind=kind).inc()


def _record_account_cap_rejection(kind: AccountLeaseKind | None) -> None:
    if kind is None:
        return
    if PROMETHEUS_AVAILABLE and account_cap_rejections_total is not None:
        account_cap_rejections_total.labels(kind=kind).inc()


def _normalize_account_routing_policy(value: str | None) -> str:
    if value in _ACCOUNT_ROUTING_POLICIES:
        return value
    return _ROUTING_POLICY_NORMAL


async def _load_dashboard_additional_quota_routing_overrides() -> dict[str, str]:
    dashboard_settings = await get_settings_cache().get()
    return _parse_additional_quota_routing_policies(dashboard_settings.additional_quota_routing_policies_json)


def _additional_quota_routing_policy_override(limit_name: str | None, policies: dict[str, str]) -> str | None:
    if limit_name is None:
        return None
    normalized_limit_name = canonicalize_additional_quota_key(limit_name=limit_name)
    if normalized_limit_name is None:
        return None
    policy = get_additional_quota_routing_policy(normalized_limit_name, overrides=policies)
    if policy == "inherit":
        return None
    return policy


def _normalize_additional_quota_key(raw_quota_key: str) -> str | None:
    canonical_key = canonicalize_additional_quota_key(quota_key=raw_quota_key, limit_name=raw_quota_key)
    if canonical_key is None:
        return None
    if get_additional_quota_definition(canonical_key) is None:
        return None
    return canonical_key


def _parse_additional_quota_routing_policies(raw_policies: str) -> dict[str, str]:
    if not raw_policies:
        return {}
    try:
        parsed = json.loads(raw_policies)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    policies: dict[str, str] = {}
    for quota_key, policy in parsed.items():
        if not isinstance(quota_key, str) or not isinstance(policy, str):
            continue
        normalized_key = _normalize_additional_quota_key(quota_key)
        normalized_policy = policy.strip().lower()
        if normalized_key and normalized_policy in _ADDITIONAL_QUOTA_ROUTING_POLICIES:
            policies[normalized_key] = normalized_policy
    return policies


def _state_from_account(
    *,
    account: Account,
    primary_entry: UsageHistory | AdditionalUsageHistory | None,
    secondary_entry: UsageHistory | AdditionalUsageHistory | None,
    runtime: RuntimeState,
) -> AccountState:
    routing_policy = _normalize_account_routing_policy(getattr(account, "routing_policy", None))
    primary_used = primary_entry.used_percent if primary_entry else None
    primary_reset = primary_entry.reset_at if primary_entry else None
    primary_window_minutes = primary_entry.window_minutes if primary_entry else None
    effective_secondary_entry = secondary_entry
    if (
        effective_secondary_entry is not None
        and effective_secondary_entry.window == "monthly"
        and usage_core.capacity_for_plan(account.plan_type, "monthly") is None
    ):
        effective_secondary_entry = None
    primary_row = usage_history_to_window_row(primary_entry) if primary_entry is not None else None
    secondary_row = usage_history_to_window_row(secondary_entry) if secondary_entry is not None else None
    # Weekly-only accounts may not emit a dedicated secondary row; treat the
    # weekly primary row as quota-window input for balancer decisions. When
    # both rows exist, prefer the newer weekly snapshot.
    if primary_row is not None and usage_core.should_use_weekly_primary(primary_row, secondary_row):
        effective_secondary_entry = primary_entry
        primary_used = None
        primary_reset = None
        primary_window_minutes = None

    secondary_used = effective_secondary_entry.used_percent if effective_secondary_entry else None
    secondary_reset = effective_secondary_entry.reset_at if effective_secondary_entry else None
    credits_has, credits_unlimited, credits_balance = _extract_credit_status(
        primary_entry,
        effective_secondary_entry,
        secondary_entry,
    )

    # If the usage window has reset (reset_at is in the past) but the last
    # recorded sample still shows 100 % usage, the data is stale.  Zero it
    # out so the account is not incorrectly blocked or deprioritised while
    # waiting for the next usage refresh to fetch fresh numbers.
    now_epoch = int(time.time())
    if primary_used is not None and primary_used >= 100.0:
        if primary_reset is not None and primary_reset <= now_epoch:
            primary_used = 0.0
            primary_reset = None
    if secondary_used is not None and secondary_used >= 100.0:
        if secondary_reset is not None and secondary_reset <= now_epoch:
            secondary_used = 0.0
            secondary_reset = None

    ignore_zero_capacity_primary_runtime_reset = False
    status_seed = account.status
    long_window_quota_available = (
        effective_secondary_entry is not None
        and _usage_entry_is_recent_enough(effective_secondary_entry.recorded_at)
        and effective_secondary_entry.used_percent is not None
        and float(effective_secondary_entry.used_percent) < 100.0
    )
    if usage_core.capacity_for_plan(account.plan_type, "primary") == 0.0 and (
        account.status != AccountStatus.RATE_LIMITED
        or (
            primary_window_minutes is not None
            and not usage_core.is_primary_window_minutes(primary_window_minutes)
            and long_window_quota_available
        )
        or (primary_entry is None and long_window_quota_available)
    ):
        primary_used = None
        primary_reset = None
        primary_window_minutes = None
        ignore_zero_capacity_primary_runtime_reset = account.status == AccountStatus.RATE_LIMITED
        if account.status == AccountStatus.RATE_LIMITED:
            status_seed = AccountStatus.ACTIVE

    # Use account.reset_at from DB as the authoritative source for runtime reset
    # and to survive process restarts.
    db_reset_at = (
        None if ignore_zero_capacity_primary_runtime_reset else (float(account.reset_at) if account.reset_at else None)
    )
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
            freshness_entry = _rate_limited_freshness_entry(
                account=account,
                primary_entry=primary_entry,
                long_window_entry=effective_secondary_entry,
            )
        else:
            freshness_entry = None
        if freshness_entry and freshness_entry.recorded_at is not None:
            recorded_epoch = freshness_entry.recorded_at.replace(tzinfo=timezone.utc).timestamp()
            if recorded_epoch > effective_blocked_at:
                effective_runtime_reset = None

    status, used_percent, reset_at = apply_usage_quota(
        status=status_seed,
        primary_used=primary_used,
        primary_reset=primary_reset,
        primary_window_minutes=primary_window_minutes,
        runtime_reset=effective_runtime_reset,
        secondary_used=secondary_used,
        secondary_reset=secondary_reset,
        credits_has=credits_has,
        credits_unlimited=credits_unlimited,
        credits_balance=credits_balance,
    )

    if status == AccountStatus.QUOTA_EXCEEDED:
        next_blocked_at = effective_blocked_at
    elif status == AccountStatus.RATE_LIMITED and account.status != AccountStatus.QUOTA_EXCEEDED:
        next_blocked_at = effective_blocked_at
    else:
        next_blocked_at = None

    settings = get_settings()
    if getattr(settings, "soft_drain_enabled", True):
        new_tier = evaluate_health_tier(
            AccountState(
                account_id=account.id,
                status=status,
                used_percent=used_percent,
                secondary_used_percent=secondary_used,
                last_error_at=runtime.last_error_at,
                error_count=runtime.error_count,
                health_tier=runtime.health_tier,
                routing_policy=routing_policy,
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

    inflight_pressure_pct = (runtime.inflight_response_creates + runtime.inflight_streams) * getattr(
        settings, "proxy_account_inflight_penalty_pct", 2.5
    )
    leased_token_pressure_pct = 0.0
    long_window_key = "secondary"
    if effective_secondary_entry is not None and effective_secondary_entry.window == "monthly":
        long_window_key = "monthly"
    capacity_credits = usage_core.capacity_for_plan(account.plan_type, long_window_key) or 0.0
    if capacity_credits > 0.0 and runtime.leased_tokens > 0:
        lease_token_weight = getattr(settings, "proxy_account_lease_token_weight", 1.0)
        leased_token_pressure_pct = runtime.leased_tokens * lease_token_weight / capacity_credits * 100.0
    pressure_pct = inflight_pressure_pct + leased_token_pressure_pct
    effective_used_percent = None if used_percent is None else min(100.0, used_percent + pressure_pct)
    effective_secondary_used_percent = None if secondary_used is None else min(100.0, secondary_used + pressure_pct)

    return AccountState(
        account_id=account.id,
        status=status,
        used_percent=effective_used_percent,
        reset_at=reset_at,
        primary_reset_at=primary_reset,
        blocked_at=next_blocked_at,
        cooldown_until=runtime.cooldown_until,
        secondary_used_percent=effective_secondary_used_percent,
        secondary_reset_at=secondary_reset,
        last_error_at=runtime.last_error_at,
        last_selected_at=runtime.last_selected_at,
        error_count=runtime.error_count,
        deactivation_reason=account.deactivation_reason,
        plan_type=account.plan_type,
        capacity_credits=capacity_credits,
        health_tier=new_tier,
        inflight_response_creates=runtime.inflight_response_creates,
        inflight_streams=runtime.inflight_streams,
        leased_tokens=runtime.leased_tokens,
        routing_policy=routing_policy,
    )


def background_recovery_state_from_account(
    *,
    account: Account,
    primary_entry: UsageHistory | None,
    secondary_entry: UsageHistory | None,
) -> AccountState:
    """Evaluate recovery for a persisted blocked account without live runtime state.

    The usage refresh scheduler only needs to know whether a persisted blocked
    account can safely return to `active`. Seed a throwaway runtime snapshot
    from the persisted block marker so fresh post-block usage rows can clear a
    stale reset guard even when the original balancer process is gone.
    """

    runtime = RuntimeState()
    blocked_at = float(account.blocked_at) if account.blocked_at is not None else None
    reset_at = float(account.reset_at) if account.reset_at is not None else None

    if blocked_at is not None:
        runtime.blocked_at = blocked_at

    if account.status == AccountStatus.RATE_LIMITED and blocked_at is not None:
        if reset_at is not None:
            runtime.cooldown_until = reset_at
    state = _state_from_account(
        account=account,
        primary_entry=primary_entry,
        secondary_entry=secondary_entry,
        runtime=runtime,
    )
    if account.status == AccountStatus.RATE_LIMITED:
        freshness_entry = _rate_limited_freshness_entry(
            account=account,
            primary_entry=primary_entry,
            long_window_entry=secondary_entry,
        )
        if blocked_at is not None and reset_at is not None and reset_at <= time.time():
            if not _usage_entry_recorded_after_block(freshness_entry, blocked_at):
                return replace(
                    state,
                    status=AccountStatus.RATE_LIMITED,
                    reset_at=reset_at,
                    blocked_at=blocked_at,
                    cooldown_until=reset_at,
                )
        elif blocked_at is None and reset_at is not None and reset_at <= time.time():
            if not _usage_entry_is_recent_available(freshness_entry):
                return replace(
                    state,
                    status=AccountStatus.RATE_LIMITED,
                    reset_at=reset_at,
                    blocked_at=None,
                    cooldown_until=None,
                )
        if reset_at is None:
            return replace(
                state,
                status=AccountStatus.RATE_LIMITED,
                reset_at=None,
                blocked_at=blocked_at,
                cooldown_until=None,
            )
    return state


def _select_long_window_entry(
    *,
    account: Account,
    monthly_entry: UsageHistory | None,
    secondary_entry: UsageHistory | AdditionalUsageHistory | None,
) -> UsageHistory | AdditionalUsageHistory | None:
    if monthly_entry is not None and usage_core.capacity_for_plan(account.plan_type, "monthly") is not None:
        return monthly_entry
    return secondary_entry


def _rate_limited_freshness_entry(
    *,
    account: Account,
    primary_entry: _UsageWindowEntry | None,
    long_window_entry: _UsageWindowEntry | None,
) -> _UsageWindowEntry | None:
    if (
        long_window_entry is not None
        and long_window_entry.window == "monthly"
        and usage_core.capacity_for_plan(account.plan_type, "monthly") is not None
    ):
        return long_window_entry
    if primary_entry is not None:
        return primary_entry
    return None


def _usage_entry_is_recent_available(entry: _UsageWindowEntry | None) -> bool:
    return (
        entry is not None
        and _usage_entry_is_recent_enough(entry.recorded_at)
        and entry.used_percent is not None
        and float(entry.used_percent) < 100.0
    )


def _usage_entry_recorded_after_block(entry: _UsageWindowEntry | None, blocked_at: float) -> bool:
    if entry is None or entry.recorded_at is None:
        return False
    recorded_at = entry.recorded_at
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=timezone.utc)
    return recorded_at.timestamp() > blocked_at


def _extract_credit_status(
    *entries: _UsageWindowEntry | None,
) -> tuple[bool | None, bool | None, float | None]:
    credit_entries: list[UsageHistory] = [
        entry
        for entry in entries
        if isinstance(entry, UsageHistory)
        and not (entry.credits_has is None and entry.credits_unlimited is None and entry.credits_balance is None)
    ]
    if not credit_entries:
        return None, None, None
    entry = max(
        credit_entries,
        key=lambda item: item.recorded_at if item.recorded_at is not None else datetime.min,
    )
    if entry is not None:
        return entry.credits_has, entry.credits_unlimited, entry.credits_balance
    return None, None, None


def _usage_entry_is_recent_enough(recorded_at: datetime | None) -> bool:
    if recorded_at is None:
        return False
    current_time = utcnow()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    interval_seconds = max(_usage_refresh_interval_seconds() * 2, 180)
    recorded_time = recorded_at if recorded_at.tzinfo is not None else recorded_at.replace(tzinfo=timezone.utc)
    return recorded_time >= current_time - timedelta(seconds=interval_seconds)


def _usage_refresh_interval_seconds() -> int:
    settings = config_settings.get_settings()
    return int(getattr(settings, "usage_refresh_interval_seconds", _DEFAULT_USAGE_REFRESH_INTERVAL_SECONDS))


def _filter_accounts_for_model(accounts: list[Account], model: str) -> list[Account]:
    allowed_plans = get_model_registry().plan_types_for_model(model)
    if allowed_plans is None:
        return accounts
    return [a for a in accounts if account_plan_matches_allowed(a.plan_type, allowed_plans)]


def _selectable_accounts(accounts: list[Account]) -> list[Account]:
    return [
        account
        for account in accounts
        if account.status not in (AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED, AccountStatus.PAUSED)
    ]


def _gated_limit_name_for_model(model: str | None) -> str | None:
    return get_additional_quota_key_for_model_id(model)


def _mapped_model_has_registry_entry(model: str | None) -> bool:
    if model is None:
        return False
    registry = get_model_registry()
    plan_types_for_model = getattr(registry, "plan_types_for_model", None)
    if not callable(plan_types_for_model):
        return False
    return bool(plan_types_for_model(model))


def _clone_account(account: Account) -> Account:
    data = {column.name: getattr(account, column.name) for column in Account.__table__.columns}
    return Account(**data)


def _first_not_none(
    primary_entry: UsageHistory | AdditionalUsageHistory | None,
    secondary_entry: UsageHistory | AdditionalUsageHistory | None,
    field: str,
):
    if primary_entry is not None:
        value = getattr(primary_entry, field, None)
        if value is not None:
            return value
    if secondary_entry is not None:
        return getattr(secondary_entry, field, None)
    return None


def _clone_usage_history(entry: UsageHistory | AdditionalUsageHistory) -> UsageHistory | AdditionalUsageHistory:
    if isinstance(entry, AdditionalUsageHistory):
        data = {column.name: getattr(entry, column.name) for column in AdditionalUsageHistory.__table__.columns}
        return AdditionalUsageHistory(**data)
    data = {column.name: getattr(entry, column.name) for column in UsageHistory.__table__.columns}
    return UsageHistory(**data)


def _clone_standard_usage_history(entry: UsageHistory) -> UsageHistory:
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
        latest_monthly={
            account_id: _clone_standard_usage_history(entry)
            for account_id, entry in selection_inputs.latest_monthly.items()
        },
        quota_planner_settings=selection_inputs.quota_planner_settings,
        runtime_accounts=(
            None
            if selection_inputs.runtime_accounts is None
            else [_clone_account(account) for account in selection_inputs.runtime_accounts]
        ),
        error_message=selection_inputs.error_message,
        error_code=selection_inputs.error_code,
        ignore_standard_quota_account_ids=frozenset(selection_inputs.ignore_standard_quota_account_ids),
        ignore_standard_quota_status=selection_inputs.ignore_standard_quota_status,
        persist_standard_quota_status=selection_inputs.persist_standard_quota_status,
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
    return await additional_usage_repo.latest_by_quota_key(
        resolved_quota_key,
        window,
        account_ids=account_ids,
        since=since,
    )


def _additional_usage_fresh_since(now: datetime | None = None) -> datetime:
    current_time = now or utcnow()
    interval_seconds = max(_usage_refresh_interval_seconds() * 2, 180)
    return current_time - timedelta(seconds=interval_seconds)


def _additional_quota_eligibility(
    *,
    account_id: str,
    account_plan_type: str | None,
    quota_key: str | None,
    explicit_limit: bool = False,
    latest_primary: dict[str, AdditionalUsageHistory],
    latest_secondary: dict[str, AdditionalUsageHistory],
    fresh_primary: dict[str, AdditionalUsageHistory],
    fresh_secondary: dict[str, AdditionalUsageHistory],
) -> str:
    latest_primary_entry = latest_primary.get(account_id)
    latest_secondary_entry = latest_secondary.get(account_id)
    primary_entry = fresh_primary.get(account_id)
    secondary_entry = fresh_secondary.get(account_id)

    if not explicit_limit and not _additional_quota_applies_to_plan(quota_key=quota_key, plan_type=account_plan_type):
        return "eligible"

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


def _additional_quota_applies_to_plan(*, quota_key: str | None, plan_type: str | None) -> bool:
    definition = get_additional_quota_definition(quota_key)
    if definition is None or definition.applies_to_plans is None:
        return True
    normalized_plan = normalize_account_plan_type(plan_type)
    if normalized_plan is None:
        return True
    if normalized_plan in definition.applies_to_plans:
        return True
    return normalized_plan not in _ADDITIONAL_QUOTA_EXEMPT_PLAN_TYPES


def _additional_usage_is_exhausted(entry: AdditionalUsageHistory) -> bool:
    if entry.used_percent is None:
        return False
    if entry.reset_at is not None and int(entry.reset_at) <= int(time.time()):
        return False
    return float(entry.used_percent) >= 100.0


def _state_above_budget_threshold(state: AccountState, budget_threshold_pct: float) -> bool:
    used_percent = state.priority_used_percent if state.priority_used_percent is not None else state.used_percent
    return used_percent is not None and used_percent > budget_threshold_pct


def _state_above_sticky_budget_threshold(
    state: AccountState,
    budget_threshold_pct: float,
    secondary_budget_threshold_pct: float | None = None,
) -> bool:
    secondary_threshold = (
        budget_threshold_pct if secondary_budget_threshold_pct is None else secondary_budget_threshold_pct
    )
    used_percent = state.priority_used_percent if state.priority_used_percent is not None else state.used_percent
    if state.limit_scoped_usage and state.priority_secondary_used_percent is None:
        secondary_used_percent = used_percent
    else:
        secondary_used_percent = (
            state.priority_secondary_used_percent
            if state.priority_secondary_used_percent is not None
            else state.secondary_used_percent
        )
    return (used_percent is not None and used_percent > budget_threshold_pct) or (
        secondary_used_percent is not None and secondary_used_percent > secondary_threshold
    )


def _select_account_preferring_budget_safe(
    states: Iterable[AccountState],
    *,
    prefer_earlier_reset: bool,
    prefer_earlier_reset_window: ResetPreferenceWindow = "secondary",
    routing_strategy: RoutingStrategy,
    relative_availability_power: float = 2.0,
    relative_availability_top_k: int = 5,
    budget_threshold_pct: float,
    secondary_budget_threshold_pct: float = 100.0,
    apply_secondary_budget_threshold: bool = False,
    allow_backoff_fallback: bool = True,
    deterministic_probe: bool = False,
    traffic_class: TrafficClass = TRAFFIC_CLASS_FOREGROUND,
    ignore_standard_quota: bool = False,
    routing_costs_by_account_id: RoutingCostsByAccount | None = None,
    ordered_account_ids: Collection[str] | None = None,
) -> SelectionResult:
    state_list = list(states)
    state_budget_threshold = (
        (
            lambda state: _state_above_sticky_budget_threshold(
                state,
                budget_threshold_pct,
                secondary_budget_threshold_pct,
            )
        )
        if apply_secondary_budget_threshold
        else (lambda state: _state_above_budget_threshold(state, budget_threshold_pct))
    )
    if routing_strategy in ("sequential_drain", "reset_drain", "single_account", "ordered_fallback"):
        budget_safe_states = [
            state
            for state in state_list
            if state.routing_policy != ROUTING_POLICY_PRESERVE and not state_budget_threshold(state)
        ]
        return select_account(
            budget_safe_states or state_list,
            prefer_earlier_reset=prefer_earlier_reset,
            prefer_earlier_reset_window=prefer_earlier_reset_window,
            routing_strategy=routing_strategy,
            allow_backoff_fallback=allow_backoff_fallback,
            deterministic_probe=deterministic_probe,
            relative_availability_power=relative_availability_power,
            relative_availability_top_k=relative_availability_top_k,
            traffic_class=traffic_class,
            ignore_standard_quota=ignore_standard_quota,
            routing_costs=routing_costs_by_account_id,
            ordered_account_ids=ordered_account_ids,
        )

    best_health_states = _best_health_tier_states(state_list)
    burn_first_states = [state for state in best_health_states if state.routing_policy == ROUTING_POLICY_BURN_FIRST]
    if burn_first_states:
        burn_first = select_account(
            burn_first_states,
            prefer_earlier_reset=prefer_earlier_reset,
            prefer_earlier_reset_window=prefer_earlier_reset_window,
            routing_strategy=routing_strategy,
            allow_backoff_fallback=False,
            deterministic_probe=deterministic_probe,
            relative_availability_power=relative_availability_power,
            relative_availability_top_k=relative_availability_top_k,
            traffic_class=traffic_class,
            ignore_standard_quota=ignore_standard_quota,
            routing_costs=routing_costs_by_account_id,
            ordered_account_ids=ordered_account_ids,
        )
        if burn_first.account is not None:
            return burn_first

    preferred_states = [
        state
        for state in state_list
        if state.routing_policy != ROUTING_POLICY_PRESERVE and not state_budget_threshold(state)
    ]
    if preferred_states:
        selection_pool = preferred_states if len(preferred_states) != len(state_list) else state_list
        preferred = select_account(
            selection_pool,
            prefer_earlier_reset=prefer_earlier_reset,
            prefer_earlier_reset_window=prefer_earlier_reset_window,
            routing_strategy=routing_strategy,
            allow_backoff_fallback=allow_backoff_fallback,
            deterministic_probe=deterministic_probe,
            relative_availability_power=relative_availability_power,
            relative_availability_top_k=relative_availability_top_k,
            traffic_class=traffic_class,
            ignore_standard_quota=ignore_standard_quota,
            routing_costs=routing_costs_by_account_id,
            ordered_account_ids=ordered_account_ids,
        )
        if preferred.account is not None:
            return preferred
        if len(preferred_states) == len(state_list):
            return preferred
    if routing_strategy == "usage_weighted" and state_list:
        return select_account(
            state_list,
            prefer_earlier_reset=prefer_earlier_reset,
            prefer_earlier_reset_window=prefer_earlier_reset_window,
            routing_strategy=routing_strategy,
            allow_backoff_fallback=allow_backoff_fallback,
            deterministic_probe=deterministic_probe,
            usage_weighted_order="primary_first",
            traffic_class=traffic_class,
            ignore_standard_quota=ignore_standard_quota,
            routing_costs=routing_costs_by_account_id,
            ordered_account_ids=ordered_account_ids,
        )
    return select_account(
        state_list,
        prefer_earlier_reset=prefer_earlier_reset,
        prefer_earlier_reset_window=prefer_earlier_reset_window,
        routing_strategy=routing_strategy,
        allow_backoff_fallback=allow_backoff_fallback,
        deterministic_probe=deterministic_probe,
        relative_availability_power=relative_availability_power,
        relative_availability_top_k=relative_availability_top_k,
        traffic_class=traffic_class,
        ignore_standard_quota=ignore_standard_quota,
        routing_costs=routing_costs_by_account_id,
        ordered_account_ids=ordered_account_ids,
    )


def _best_health_tier_states(states: list[AccountState]) -> list[AccountState]:
    healthy = [state for state in states if state.health_tier == HEALTH_TIER_HEALTHY]
    if healthy:
        return healthy
    probing = [state for state in states if state.health_tier == HEALTH_TIER_PROBING]
    if probing:
        return probing
    draining = [state for state in states if state.health_tier == HEALTH_TIER_DRAINING]
    return draining or states


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
