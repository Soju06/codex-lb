from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Generic, Literal, Protocol, TypeVar

from app.core.balancer import (
    HEALTH_TIER_DRAINING,
    HEALTH_TIER_HEALTHY,
    HEALTH_TIER_PROBING,
    ROUTING_POLICY_BURN_FIRST,
    ROUTING_POLICY_PRESERVE,
    TRAFFIC_CLASS_FOREGROUND,
    AccountState,
    ResetPreferenceWindow,
    RoutingCostsByAccount,
    RoutingStrategy,
    SelectionResult,
    TrafficClass,
    select_account,
)
from app.db.models import Account, AccountStatus, AdditionalUsageHistory, StickySessionKind, UsageHistory
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy._load_balancer.types import (
    MAX_SELECTION_ATTEMPTS,
    AccountConcurrencyCaps,
    AccountLease,
    AccountLeaseKind,
)
from app.modules.proxy.affinity import _CodexSessionSource
from app.modules.proxy.repo_bundle import ProxyRepoFactory
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.quota_planner.logic import PlannerSettings, build_routing_costs

# Preserve the established observability surface while implementation moves to
# a private module; operators and tests filter this logger by its public owner.
logger = logging.getLogger("app.modules.proxy.load_balancer")

_STICKY_GRACE_PERIOD_SECONDS = 10.0
_STICKY_EXISTING_UNSET = object()
_RECOVERABLE_STATUSES = frozenset(
    {
        AccountStatus.ACTIVE,
        AccountStatus.RATE_LIMITED,
        AccountStatus.QUOTA_EXCEEDED,
    }
)

StickySelectionDisposition = Literal["shared_result", "direct_error"]
AccountCapRejectionCallback = Callable[[AccountLeaseKind | None], None]


class SelectionInputsProtocol(Protocol):
    accounts: list[Account]
    latest_primary: dict[str, UsageHistory | AdditionalUsageHistory]
    latest_secondary: dict[str, UsageHistory | AdditionalUsageHistory]
    latest_monthly: dict[str, UsageHistory]
    quota_planner_settings: PlannerSettings
    runtime_accounts: list[Account] | None
    error_message: str | None
    error_code: str | None
    ignore_standard_quota_account_ids: frozenset[str]
    routing_policy_override: str | None

    @property
    def effective_continuity_owner_candidates(self) -> list[Account]: ...


SelectionInputsT = TypeVar("SelectionInputsT", bound=SelectionInputsProtocol)


class StickySelectionOwner(Protocol):
    _runtime_lock: asyncio.Lock
    _repo_factory: ProxyRepoFactory

    def _prepare_sticky_selection_states(
        self,
        selection_inputs: SelectionInputsProtocol,
        *,
        required_account_id: str | None,
    ) -> tuple[list[AccountState], dict[str, Account]]: ...

    def _sync_runtime_state(
        self,
        account: Account,
        state: AccountState,
        *,
        selected: bool = False,
        expected_version: int | None = None,
    ) -> bool: ...

    def _account_lease_allowed_locked(
        self,
        account_id: str,
        *,
        kind: AccountLeaseKind,
        caps: AccountConcurrencyCaps,
        stream_reserve_slots: int = 0,
    ) -> bool: ...

    def _acquire_account_lease_locked(
        self,
        account_id: str,
        *,
        kind: AccountLeaseKind,
        estimated_tokens: float,
    ) -> AccountLease: ...

    async def _persist_selection_state(
        self,
        accounts_repo: AccountsRepository,
        account_map: dict[str, Account],
        states: list[AccountState],
    ) -> set[str]: ...

    async def release_account_lease(self, lease: AccountLease | None) -> None: ...


@dataclass(frozen=True, slots=True)
class StickySelectionRequest(Generic[SelectionInputsT]):
    sticky_key: str
    sticky_kind: StickySessionKind | None
    reallocate_sticky: bool
    sticky_source: _CodexSessionSource | None
    legacy_sticky_key: str | None
    legacy_existing_account_id: str | None
    spill_bare_session_on_account_cap: bool
    require_unambiguous_account: bool
    sticky_max_age_seconds: int | None
    prefer_earlier_reset_accounts: bool
    prefer_earlier_reset_window: ResetPreferenceWindow
    routing_strategy: RoutingStrategy
    relative_availability_power: float
    relative_availability_top_k: int
    required_account_id: str | None
    budget_threshold_pct: float
    secondary_budget_threshold_pct: float
    routing_costs_by_account_id: RoutingCostsByAccount | None
    lease_kind: AccountLeaseKind | None
    estimated_lease_tokens: float
    stream_reserve_slots: int
    traffic_class: TrafficClass
    concurrency_caps: AccountConcurrencyCaps
    selection_inputs: SelectionInputsT
    reload_inputs: Callable[[], Awaitable[SelectionInputsT]]
    record_account_cap_rejection: AccountCapRejectionCallback


@dataclass(frozen=True, slots=True)
class StickySelectionOutcome(Generic[SelectionInputsT]):
    selection_inputs: SelectionInputsT
    selected_snapshot: Account | None
    selected_lease: AccountLease | None
    error_message: str | None
    error_code: str | None
    disposition: StickySelectionDisposition = "shared_result"


async def run_sticky_selection_path(
    owner: StickySelectionOwner,
    *,
    request: StickySelectionRequest[SelectionInputsT],
) -> StickySelectionOutcome[SelectionInputsT]:
    selection_inputs = request.selection_inputs
    sticky_existing_account_id: str | None | object = _STICKY_EXISTING_UNSET
    selected_snapshot: Account | None = None
    selected_lease: AccountLease | None = None
    error_message: str | None = None
    selection_error_code: str | None = None
    attempt = 0

    while True:
        attempt += 1
        async with owner._runtime_lock:
            states, account_map = owner._prepare_sticky_selection_states(
                selection_inputs,
                required_account_id=request.required_account_id,
            )
            effective_routing_costs = (
                request.routing_costs_by_account_id
                if request.routing_costs_by_account_id is not None
                else build_routing_costs(
                    settings=selection_inputs.quota_planner_settings,
                    states=states,
                    now=datetime.now(timezone.utc),
                )
            )

        sticky_existing_is_legacy = isinstance(request.legacy_existing_account_id, str)
        if request.sticky_key and request.sticky_kind == StickySessionKind.CODEX_SESSION:
            async with owner._repo_factory() as repos:
                sticky_existing_account_id = await repos.sticky_sessions.get_account_id(
                    request.sticky_key,
                    kind=request.sticky_kind,
                    max_age_seconds=request.sticky_max_age_seconds,
                )
            if isinstance(request.legacy_existing_account_id, str):
                # Mixed-version replicas can create both rows on different
                # accounts. The raw row always wins as possible hard turn-state
                # ownership.
                sticky_existing_account_id = request.legacy_existing_account_id

        # Key shape is deliberately irrelevant here. Only typed source
        # provenance created by the affinity parser can grant mobility.
        bare_session_key = (
            request.sticky_kind == StickySessionKind.CODEX_SESSION
            and request.sticky_source == "session_header"
            and request.legacy_sticky_key is not None
            and not sticky_existing_is_legacy
        )
        cap_spillover_allowed = (
            request.spill_bare_session_on_account_cap and request.lease_kind is not None and bare_session_key
        )
        hard_sticky = (
            request.sticky_kind == StickySessionKind.CODEX_SESSION
            and isinstance(sticky_existing_account_id, str)
            and not bare_session_key
        )
        if (
            hard_sticky
            and request.required_account_id is not None
            and sticky_existing_account_id != request.required_account_id
        ):
            return StickySelectionOutcome(
                selection_inputs=selection_inputs,
                selected_snapshot=None,
                selected_lease=None,
                error_message="Account-owned continuity sources conflict; retry the logical turn",
                error_code="continuity_owner_conflict",
                disposition="direct_error",
            )

        if (
            request.require_unambiguous_account
            and not hard_sticky
            and len(selection_inputs.effective_continuity_owner_candidates) != 1
        ):
            return StickySelectionOutcome(
                selection_inputs=selection_inputs,
                selected_snapshot=None,
                selected_lease=None,
                error_message="Conversation owner cannot be determined from the eligible account pool",
                error_code="conversation_owner_unavailable",
                disposition="direct_error",
            )

        if hard_sticky:
            # A resolved hard Codex mapping is an ownership constraint, not a
            # preference. Never delete or rebind it under transient pressure.
            selection_states = [state for state in states if state.account_id == sticky_existing_account_id]
        elif bare_session_key and isinstance(sticky_existing_account_id, str) and not cap_spillover_allowed:
            selection_states = states
        else:
            selection_states = _filter_states_for_account_caps(
                states,
                lease_kind=request.lease_kind,
                caps=request.concurrency_caps,
                stream_reserve_slots=request.stream_reserve_slots,
            )

        if cap_spillover_allowed and request.lease_kind == "stream":
            response_create_states = _filter_states_for_account_caps(
                selection_states,
                lease_kind="response_create",
                caps=request.concurrency_caps,
                stream_reserve_slots=0,
            )
            selection_states = response_create_states or selection_states

        preserve_existing_mapping = (
            bare_session_key
            and isinstance(sticky_existing_account_id, str)
            and (
                (
                    cap_spillover_allowed
                    and any(state.account_id == sticky_existing_account_id for state in states)
                    and not any(state.account_id == sticky_existing_account_id for state in selection_states)
                )
                or request.require_unambiguous_account
            )
        )

        if hard_sticky and not selection_states:
            selection_error_code = "hard_affinity_saturated"
            result = SelectionResult(None, "Hard affinity owner account is unavailable")
        elif not selection_states and states:
            selection_error_code = _account_cap_error_code(request.lease_kind)
            result = SelectionResult(
                None,
                _account_cap_error_message(request.lease_kind, request.concurrency_caps),
            )
            logger.warning(
                "Account cap exhausted during sticky selection lease_kind=%s reason=%s candidates=%s",
                request.lease_kind,
                selection_error_code,
                len(states),
            )
            request.record_account_cap_rejection(request.lease_kind)
        elif hard_sticky:
            result = _select_account_preferring_budget_safe(
                selection_states,
                prefer_earlier_reset=request.prefer_earlier_reset_accounts,
                prefer_earlier_reset_window=request.prefer_earlier_reset_window,
                routing_strategy=request.routing_strategy,
                relative_availability_power=request.relative_availability_power,
                relative_availability_top_k=request.relative_availability_top_k,
                budget_threshold_pct=request.budget_threshold_pct,
                secondary_budget_threshold_pct=request.secondary_budget_threshold_pct,
                traffic_class=request.traffic_class,
                ignore_standard_quota=False,
                routing_costs_by_account_id=effective_routing_costs,
            )
            if result.account is None:
                selection_error_code = "hard_affinity_saturated"
                result = SelectionResult(
                    None,
                    result.error_message or "Hard affinity owner account is unavailable",
                )
            else:
                selection_error_code = None
        else:
            selection_error_code = None
            async with owner._repo_factory() as repos:
                result = await _select_with_stickiness(
                    states=selection_states,
                    account_map=account_map,
                    sticky_key=request.sticky_key,
                    sticky_kind=request.sticky_kind,
                    reallocate_sticky=request.reallocate_sticky,
                    sticky_max_age_seconds=request.sticky_max_age_seconds,
                    budget_threshold_pct=request.budget_threshold_pct,
                    secondary_budget_threshold_pct=request.secondary_budget_threshold_pct,
                    prefer_earlier_reset_accounts=request.prefer_earlier_reset_accounts,
                    prefer_earlier_reset_window=request.prefer_earlier_reset_window,
                    routing_strategy=request.routing_strategy,
                    relative_availability_power=request.relative_availability_power,
                    relative_availability_top_k=request.relative_availability_top_k,
                    sticky_repo=repos.sticky_sessions,
                    sticky_existing_account_id=sticky_existing_account_id,
                    preserve_existing_mapping_on_fallback=preserve_existing_mapping,
                    traffic_class=request.traffic_class,
                    ignore_standard_quota=False,
                    routing_costs_by_account_id=effective_routing_costs,
                )

        selected_states: list[AccountState] = []
        async with owner._runtime_lock:
            for state in states:
                account = account_map.get(state.account_id)
                if account is None:
                    continue
                owner._sync_runtime_state(
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
                    if request.lease_kind is not None:
                        if not owner._account_lease_allowed_locked(
                            selected.id,
                            kind=request.lease_kind,
                            caps=request.concurrency_caps,
                            stream_reserve_slots=request.stream_reserve_slots,
                        ):
                            selected_snapshot = None
                            selection_error_code = _account_cap_error_code(request.lease_kind)
                            error_message = _account_cap_error_message(
                                request.lease_kind,
                                request.concurrency_caps,
                            )
                        else:
                            selected_lease = owner._acquire_account_lease_locked(
                                selected.id,
                                kind=request.lease_kind,
                                estimated_tokens=request.estimated_lease_tokens,
                            )
            else:
                error_message = result.error_message

        try:
            async with owner._repo_factory() as repos:
                stale_account_ids = await owner._persist_selection_state(
                    repos.accounts,
                    account_map,
                    selected_states,
                )
        except BaseException:
            await owner.release_account_lease(selected_lease)
            selected_lease = None
            raise

        stale_account_ids = stale_account_ids or set()
        if selected_snapshot is not None and selected_snapshot.id in stale_account_ids:
            await owner.release_account_lease(selected_lease)
            selected_lease = None
            selected_snapshot = None
            error_message = None
            if attempt >= MAX_SELECTION_ATTEMPTS:
                break
            selection_inputs = await request.reload_inputs()
            if selection_inputs.error_code is not None and not selection_inputs.accounts:
                return StickySelectionOutcome(
                    selection_inputs=selection_inputs,
                    selected_snapshot=None,
                    selected_lease=None,
                    error_message=selection_inputs.error_message,
                    error_code=selection_inputs.error_code,
                    disposition="direct_error",
                )
            await asyncio.sleep(0)
            continue

        if (
            selected_snapshot is None
            and selection_error_code is not None
            and not hard_sticky
            and attempt < MAX_SELECTION_ATTEMPTS
        ):
            selection_inputs = await request.reload_inputs()
            if selection_inputs.error_code is not None and not selection_inputs.accounts:
                return StickySelectionOutcome(
                    selection_inputs=selection_inputs,
                    selected_snapshot=None,
                    selected_lease=None,
                    error_message=selection_inputs.error_message,
                    error_code=selection_inputs.error_code,
                    disposition="direct_error",
                )
            error_message = None
            await asyncio.sleep(0)
            continue
        break

    return StickySelectionOutcome(
        selection_inputs=selection_inputs,
        selected_snapshot=selected_snapshot,
        selected_lease=selected_lease,
        error_message=error_message,
        error_code=selection_error_code,
    )


async def _select_with_stickiness(
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
    preserve_existing_mapping_on_fallback: bool = False,
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
    # When the pinned account is temporarily unavailable but still in the pool,
    # pick a fallback without overwriting the mapping unless explicitly asked.
    persist_fallback = not preserve_existing_mapping_on_fallback
    apply_sticky_secondary_budget_threshold = False

    if existing:
        pinned = next((state for state in states if state.account_id == existing), None)
        if pinned is not None:
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
                and pinned.reset_at - now >= 600
            )

            burn_first_reallocate = pinned.routing_policy != ROUTING_POLICY_BURN_FIRST
            if burn_first_reallocate:
                burn_first_candidates = [state for state in states if state.routing_policy == ROUTING_POLICY_BURN_FIRST]
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
                pass
            elif sticky_max_age_seconds is not None:
                persist_fallback = False
        elif not preserve_existing_mapping_on_fallback:
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
    elif preserve_existing_mapping_on_fallback and chosen.account is not None and existing is not None:
        logger.info(
            "internal_soft_affinity_spillover old_account_id=%s new_account_id=%s sticky_kind=%s",
            existing,
            chosen.account.account_id,
            sticky_kind.value,
        )
    return chosen


def _filter_states_for_account_caps(
    states: Iterable[AccountState],
    *,
    lease_kind: AccountLeaseKind | None,
    caps: AccountConcurrencyCaps,
    stream_reserve_slots: int = 0,
) -> list[AccountState]:
    if lease_kind is None:
        return list(states)
    filtered: list[AccountState] = []
    for state in states:
        if lease_kind == "response_create":
            cap = caps.response_create_limit
            if cap > 0 and state.inflight_response_creates >= cap:
                continue
        else:
            cap = caps.stream_limit
            effective_cap = max(1, cap - max(0, stream_reserve_slots))
            if cap > 0 and state.inflight_streams >= effective_cap:
                continue
        filtered.append(state)
    return filtered


def _account_cap_error_code(lease_kind: AccountLeaseKind | None) -> str | None:
    if lease_kind == "response_create":
        return "account_response_create_cap"
    if lease_kind == "stream":
        return "account_stream_cap"
    return None


def _account_cap_error_message(lease_kind: AccountLeaseKind | None, caps: AccountConcurrencyCaps) -> str:
    if lease_kind == "response_create":
        cap = caps.response_create_limit
        if caps.replica_count > 1 and caps.configured_response_create_limit is not None:
            return (
                f"Account response-create capacity is exhausted; this replica's share is {cap} "
                f"of the per-account limit {caps.configured_response_create_limit} "
                f"across {caps.replica_count} replicas"
            )
        return f"Account response-create capacity is exhausted; per-account limit is {cap}"
    if lease_kind == "stream":
        cap = caps.stream_limit
        if caps.replica_count > 1 and caps.configured_stream_limit is not None:
            return (
                f"Account stream capacity is exhausted; this replica's share is {cap} "
                f"of the per-account limit {caps.configured_stream_limit} "
                f"across {caps.replica_count} replicas. "
                "Increase the dashboard stream limit or wait for active streams to finish."
            )
        return (
            f"Account stream capacity is exhausted; per-account limit is {cap}. "
            "Increase the dashboard stream limit or wait for active streams to finish."
        )
    return "Account capacity is exhausted"


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
    if routing_strategy in ("sequential_drain", "reset_drain", "single_account"):
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


def _clone_account(account: Account) -> Account:
    data = {column.name: getattr(account, column.name) for column in Account.__table__.columns}
    return Account(**data)
