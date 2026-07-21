from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Generic, Protocol, TypeVar

from app.core.balancer import (
    ResetPreferenceWindow,
    RoutingCostsByAccount,
    RoutingStrategy,
    SelectionResult,
    TrafficClass,
)
from app.db.models import Account
from app.modules.proxy._load_balancer.sticky_selection import (
    SelectionInputsProtocol,
    StickySelectionOwner,
    _account_cap_error_code,
    _account_cap_error_message,
    _clone_account,
    _filter_recovery_probe_candidates,
    _filter_states_for_account_caps,
    _probing_result_requires_recovery_reservation,
    _select_account_preferring_budget_safe,
)
from app.modules.proxy._load_balancer.types import (
    MAX_SELECTION_ATTEMPTS,
    AccountConcurrencyCaps,
    AccountLease,
    AccountLeaseKind,
    ProbeReservation,
    RuntimeState,
)
from app.modules.proxy.account_cache import AccountSelectionCache
from app.modules.quota_planner.logic import build_routing_costs

# Preserve the established observability surface while implementation moves to
# a private module; operators and tests filter this logger by its public owner.
logger = logging.getLogger("app.modules.proxy.load_balancer")

SelectionInputsT = TypeVar("SelectionInputsT", bound=SelectionInputsProtocol)
AccountCapRejectionCallback = Callable[[AccountLeaseKind | None], None]


class UnboundSelectionOwner(StickySelectionOwner, Protocol):
    _runtime: dict[str, RuntimeState]
    _selection_inputs_cache: AccountSelectionCache


@dataclass(frozen=True, slots=True)
class UnboundSelectionRequest(Generic[SelectionInputsT]):
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
class UnboundSelectionOutcome(Generic[SelectionInputsT]):
    selection_inputs: SelectionInputsT
    selected_snapshot: Account | None
    selected_lease: AccountLease | None
    error_message: str | None
    error_code: str | None
    disposition: str = "shared_result"


async def run_unbound_selection_path(
    owner: UnboundSelectionOwner,
    *,
    request: UnboundSelectionRequest[SelectionInputsT],
) -> UnboundSelectionOutcome[SelectionInputsT]:
    selection_inputs = request.selection_inputs
    prefer_earlier_reset_accounts = request.prefer_earlier_reset_accounts
    prefer_earlier_reset_window = request.prefer_earlier_reset_window
    routing_strategy = request.routing_strategy
    relative_availability_power = request.relative_availability_power
    relative_availability_top_k = request.relative_availability_top_k
    required_account_id = request.required_account_id
    budget_threshold_pct = request.budget_threshold_pct
    secondary_budget_threshold_pct = request.secondary_budget_threshold_pct
    routing_costs_by_account_id = request.routing_costs_by_account_id
    lease_kind = request.lease_kind
    estimated_lease_tokens = request.estimated_lease_tokens
    stream_reserve_slots = request.stream_reserve_slots
    traffic_class = request.traffic_class
    caps = request.concurrency_caps
    load_selection_inputs = request.reload_inputs
    _record_account_cap_rejection = request.record_account_cap_rejection

    selected_snapshot: Account | None = None
    selected_lease: AccountLease | None = None
    error_message: str | None = None
    selection_error_code: str | None = None

    def _direct_error(
        *,
        account: None,
        error_message: str | None,
        error_code: str | None = None,
    ) -> UnboundSelectionOutcome[SelectionInputsT]:
        assert account is None
        return UnboundSelectionOutcome(
            selection_inputs=selection_inputs,
            selected_snapshot=None,
            selected_lease=None,
            error_message=error_message,
            error_code=error_code,
            disposition="direct_error",
        )

    attempt = 0
    suppress_recovery_probe_candidates = False
    while True:
        attempt += 1
        probe_reservation: ProbeReservation | None = None
        probe_reservation_invalidated = False
        async with owner._runtime_lock:
            states, account_map = owner._prepare_sticky_selection_states(
                selection_inputs,
                required_account_id=required_account_id,
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
            selection_states = _filter_states_for_account_caps(
                states,
                lease_kind=lease_kind,
                caps=caps,
                stream_reserve_slots=stream_reserve_slots,
            )
            if suppress_recovery_probe_candidates:
                selection_states = _filter_recovery_probe_candidates(
                    selection_states,
                    traffic_class=traffic_class,
                )
            if not selection_states and states:
                selection_error_code = _account_cap_error_code(lease_kind)
                error_message = _account_cap_error_message(lease_kind, caps)
                result = SelectionResult(None, error_message)
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
                )
                probing_result_requires_reservation = _probing_result_requires_recovery_reservation(
                    selection_states,
                    result.account,
                    routing_strategy=routing_strategy,
                    traffic_class=traffic_class,
                )
                if probing_result_requires_reservation and result.account is not None:
                    # Unbound recovery admissions have the same
                    # externally-visible probe quiet interval as sticky
                    # ones, but account-state persistence happens after
                    # the runtime lock is released. Hold a reversible
                    # reservation until DB persistence and the final CAS
                    # both succeed; otherwise the failed request can
                    # consume minutes of recovery capacity.
                    probe_reservation = owner._reserve_due_probe_locked(
                        selection_states,
                        prefer_earlier_reset=prefer_earlier_reset_accounts,
                        prefer_earlier_reset_window=prefer_earlier_reset_window,
                        routing_strategy=routing_strategy,
                        relative_availability_power=relative_availability_power,
                        relative_availability_top_k=relative_availability_top_k,
                        traffic_class=traffic_class,
                        routing_costs_by_account_id=effective_routing_costs,
                    )
                    result_account_id = result.account.account_id
                    if probe_reservation is None or probe_reservation.account_id != result_account_id:
                        owner._release_due_probe_reservation_locked(probe_reservation)
                        probe_reservation = None
                        probe_reservation_invalidated = True

            selected_account_map = account_map
            selected_states = []
            if not probe_reservation_invalidated:
                for state in states:
                    account = account_map.get(state.account_id)
                    if account is None:
                        continue
                    selected_reserved_probe = bool(
                        probe_reservation is not None and state.account_id == probe_reservation.account_id
                    )
                    if not selected_reserved_probe:
                        owner._sync_runtime_state(
                            account,
                            state,
                            selected=(result.account is not None and state.account_id == result.account.account_id),
                        )
                    selected_states.append(state)

            if result.account is not None and not probe_reservation_invalidated:
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
                        selected_reserved_probe = bool(
                            probe_reservation is not None and selected.id == probe_reservation.account_id
                        )
                        selected_lease = owner._acquire_account_lease_locked(
                            selected.id,
                            kind=lease_kind,
                            estimated_tokens=estimated_lease_tokens,
                            # Keep the provisional recovery token intact
                            # until DB persistence and the final probe CAS
                            # commit the admission. Recording selection
                            # here would make the CAS fail while still
                            # consuming the quiet interval.
                            record_selection=not selected_reserved_probe,
                        )
                    selected_snapshot = _clone_account(selected)
                    selected_snapshot.status = result.account.status
                    selected_snapshot.deactivation_reason = result.account.deactivation_reason
                    selected_snapshot.reset_at = selected_reset_at
            elif result.account is None:
                error_message = result.error_message

        if probe_reservation_invalidated:
            selected_snapshot = None
            error_message = None
            selected_states = []
            selected_account_map = {}
            if attempt >= MAX_SELECTION_ATTEMPTS:
                suppress_recovery_probe_candidates = True
                attempt = 0
                selection_inputs = await load_selection_inputs()
                if selection_inputs.error_code is not None and not selection_inputs.accounts:
                    return _direct_error(
                        account=None,
                        error_message=selection_inputs.error_message,
                        error_code=selection_inputs.error_code,
                    )
                await asyncio.sleep(0)
                continue
            selection_inputs = await load_selection_inputs()
            if selection_inputs.error_code is not None and not selection_inputs.accounts:
                return _direct_error(
                    account=None,
                    error_message=selection_inputs.error_message,
                    error_code=selection_inputs.error_code,
                )
            await asyncio.sleep(0)
            continue

        pre_persist_runtime_state = {
            aid: (
                runtime.reset_at,
                runtime.cooldown_until,
                runtime.error_count,
                runtime.last_error_at,
            )
            for aid, runtime in owner._runtime.items()
        }
        pre_persist_cache_generation = owner._selection_inputs_cache.generation

        try:
            async with owner._repo_factory() as repos:
                stale_account_ids = await owner._persist_selection_state(
                    repos.accounts,
                    selected_account_map,
                    selected_states,
                )
        except BaseException:
            await owner.release_account_lease(selected_lease)
            selected_lease = None
            async with owner._runtime_lock:
                owner._release_due_probe_reservation_locked(probe_reservation)
            raise
        stale_account_ids = stale_account_ids or set()
        if selected_snapshot is not None and selected_snapshot.id in stale_account_ids:
            await owner.release_account_lease(selected_lease)
            selected_lease = None
            async with owner._runtime_lock:
                owner._release_due_probe_reservation_locked(probe_reservation)
            if attempt >= MAX_SELECTION_ATTEMPTS:
                selected_snapshot = None
                error_message = None
                break
            selection_inputs = await load_selection_inputs()
            if selection_inputs.error_code is not None and not selection_inputs.accounts:
                return _direct_error(
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
            and owner._selection_inputs_cache.generation != pre_persist_cache_generation
            and attempt < MAX_SELECTION_ATTEMPTS
        ):
            await owner.release_account_lease(selected_lease)
            selected_lease = None
            async with owner._runtime_lock:
                owner._release_due_probe_reservation_locked(probe_reservation)
            selection_inputs = await load_selection_inputs()
            if selection_inputs.error_code is not None and not selection_inputs.accounts:
                return _direct_error(
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
                owner._runtime.get(account_id, RuntimeState()).reset_at != before[0]
                or owner._runtime.get(account_id, RuntimeState()).cooldown_until != before[1]
                or owner._runtime.get(account_id, RuntimeState()).error_count != before[2]
                or owner._runtime.get(account_id, RuntimeState()).last_error_at != before[3]
                for account_id, before in pre_persist_runtime_state.items()
            )
            if runtime_recovered and attempt < MAX_SELECTION_ATTEMPTS:
                selection_inputs = await load_selection_inputs()
                if selection_inputs.error_code is not None and not selection_inputs.accounts:
                    return _direct_error(
                        account=None,
                        error_message=selection_inputs.error_message,
                        error_code=selection_inputs.error_code,
                    )
                error_message = None
                selected_states = []
                selected_account_map = {}
                await asyncio.sleep(0)
                continue

        if selected_snapshot is not None and probe_reservation is not None:
            reservation_committed = False
            try:
                async with owner._runtime_lock:
                    reservation_committed = owner._commit_due_probe_reservation_locked(probe_reservation)
                    if reservation_committed:
                        owner._sync_committed_probe_state_locked(
                            probe_reservation,
                            selected_account_map,
                            selected_states,
                        )
                    else:
                        owner._release_due_probe_reservation_locked(probe_reservation)
            except BaseException:
                await owner.release_account_lease(selected_lease)
                selected_lease = None
                async with owner._runtime_lock:
                    owner._release_due_probe_reservation_locked(probe_reservation)
                raise
            if not reservation_committed:
                await owner.release_account_lease(selected_lease)
                selected_lease = None
                selected_snapshot = None
                error_message = None
                selected_states = []
                selected_account_map = {}
                if attempt >= MAX_SELECTION_ATTEMPTS:
                    suppress_recovery_probe_candidates = True
                    attempt = 0
                    selection_inputs = await load_selection_inputs()
                    if selection_inputs.error_code is not None and not selection_inputs.accounts:
                        return _direct_error(
                            account=None,
                            error_message=selection_inputs.error_message,
                            error_code=selection_inputs.error_code,
                        )
                    await asyncio.sleep(0)
                    continue
                selection_inputs = await load_selection_inputs()
                if selection_inputs.error_code is not None and not selection_inputs.accounts:
                    return _direct_error(
                        account=None,
                        error_message=selection_inputs.error_message,
                        error_code=selection_inputs.error_code,
                    )
                await asyncio.sleep(0)
                continue

        break

    return UnboundSelectionOutcome(
        selection_inputs=selection_inputs,
        selected_snapshot=selected_snapshot,
        selected_lease=selected_lease,
        error_message=error_message,
        error_code=selection_error_code,
    )
