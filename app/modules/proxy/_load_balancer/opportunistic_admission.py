"""Opportunistic admission check — private implementation unit of ``LoadBalancer``.

``LoadBalancer.check_opportunistic_admission`` delegates here. The check asks
the deterministic opportunistic selector the question a real request would
ask — same model, same ``service_tier``, same account scope, local account caps
only for the requested ``lease_kind`` — and reports the answer without acting
on it: no lease is acquired and no health, sticky or selection state is
written. The runtime lock is held only for the balancer's ordinary state
preparation (stale-lease reclaim, runtime prune, state build), exactly as
ordinary selection does, and is released before the selector runs.

``observe_only`` turns the check into a pure observation: states are built
from a detached snapshot of the runtime (the Force Probe settlement pattern) on
which stale leases are expired exactly as the live path expires them, so the
answer matches the live check while not even the usage-derived health-tier
refresh, its ``version`` / ``health_version`` bumps, or the lease housekeeping
touch the live balancer.
The read-only pool-exhaustion probe (``exhaustion_probe.py``) rides on this
path with ``lease_kind=None``, so a ``usage_limit_reached`` answer there is the
spec's 429 predicate evaluated over the request's own eligible pool.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Protocol

from app.core.balancer import (
    TRAFFIC_CLASS_OPPORTUNISTIC,
    USAGE_LIMIT_REACHED,
    AccountState,
    ResetPreferenceWindow,
    RoutingStrategy,
)
from app.core.clock import Clock
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AdditionalUsageHistory, UsageHistory
from app.modules.proxy._load_balancer.sticky_selection import (
    SelectionInputsProtocol,
    _account_cap_error_code,
    _clone_account,
    _filter_states_for_account_caps,
    _select_account_preferring_budget_safe,
)
from app.modules.proxy._load_balancer.tunables import RoutingTunables
from app.modules.proxy._load_balancer.types import AccountConcurrencyCaps, AccountLease, AccountLeaseKind, RuntimeState

# Preserve the established observability surface while implementation moves to
# a private module; operators and tests filter this logger by its public owner.
logger = logging.getLogger("app.modules.proxy.load_balancer")

OPPORTUNISTIC_BURN_WINDOW_CLOSED = "opportunistic_burn_window_closed"

AccountCapRejectionCallback = Callable[[AccountLeaseKind | None], None]
StaleLeaseTtlSeconds = Callable[[AccountLeaseKind], float]


class StatesBuilder(Protocol):
    """``load_balancer._build_states``: injected so this unit never imports its owner module."""

    def __call__(
        self,
        *,
        accounts: Iterable[Account],
        latest_primary: Mapping[str, UsageHistory | AdditionalUsageHistory],
        latest_secondary: Mapping[str, UsageHistory | AdditionalUsageHistory],
        latest_monthly: Mapping[str, UsageHistory],
        standard_latest_primary: Mapping[str, UsageHistory] | None = None,
        standard_latest_secondary: Mapping[str, UsageHistory] | None = None,
        runtime: dict[str, RuntimeState],
        now: float | None = None,
        routing_policy_override: str | None = None,
        ignore_standard_quota_account_ids: frozenset[str] = frozenset(),
        encryptor: TokenEncryptor | None = None,
        routing_tunables: RoutingTunables | None = None,
        soft_drain_enabled: bool | None = None,
        model: str | None = None,
        log_weight_transitions: bool = True,
    ) -> tuple[list[AccountState], dict[str, Account]]: ...


class OpportunisticAdmissionOwner(Protocol):
    _clock: Clock
    _encryptor: TokenEncryptor
    _runtime: dict[str, RuntimeState]
    _runtime_lock: asyncio.Lock

    async def _load_selection_inputs(
        self,
        *,
        model: str | None,
        service_tier: str | None = None,
        additional_limit_name: str | None = None,
        account_ids: Collection[str] | None = None,
    ) -> SelectionInputsProtocol: ...

    def _prepare_sticky_selection_states(
        self,
        selection_inputs: SelectionInputsProtocol,
        *,
        required_account_id: str | None,
        redact_sensitive_details: bool,
        routing_tunables: RoutingTunables,
        soft_drain_enabled: bool | None = None,
        model: str | None = None,
    ) -> tuple[list[AccountState], dict[str, Account]]: ...

    def _detached_runtime_snapshot(self, *, routing_tunables: RoutingTunables) -> dict[str, RuntimeState]: ...


def apply_lease_release(runtime: RuntimeState, lease: AccountLease) -> AccountLease | None:
    """Release ``lease`` from ``runtime``'s bookkeeping; returns the released lease or ``None`` if absent.

    Single definition of the counter math shared by the live release path and
    the detached snapshot below, so an observation expires stale leases exactly
    the way ``LoadBalancer._release_account_lease_locked`` does (minus its
    metrics and logging, which belong to live state changes only).
    """
    if runtime.leases is None:
        return None
    current = runtime.leases.pop(lease.lease_id, None)
    if current is None:
        return None
    if current.kind == "response_create":
        runtime.inflight_response_creates = max(0, runtime.inflight_response_creates - 1)
    else:
        runtime.inflight_streams = max(0, runtime.inflight_streams - 1)
        if current.api_key_id is not None and runtime.stream_key_inflight is not None:
            remaining = runtime.stream_key_inflight.get(current.api_key_id, 0) - 1
            if remaining > 0:
                runtime.stream_key_inflight[current.api_key_id] = remaining
            else:
                runtime.stream_key_inflight.pop(current.api_key_id, None)
    runtime.leased_tokens = max(0.0, runtime.leased_tokens - current.estimated_tokens)
    runtime.version += 1
    return current


def detached_runtime_snapshot(
    runtime_by_account: Mapping[str, RuntimeState],
    *,
    now: float,
    stale_lease_ttl_seconds: StaleLeaseTtlSeconds,
) -> dict[str, RuntimeState]:
    """Copy the runtime as ordinary selection would see it, without touching the live entries.

    Every mutable container is copied so a state build on the snapshot can never
    write through, and leases older than their stale TTL (``now`` is the monotonic
    clock the leases were acquired on) are expired on the copy the way the live
    reclaim expires them before it builds states.
    """
    snapshot: dict[str, RuntimeState] = {}
    for account_id, runtime in runtime_by_account.items():
        detached = replace(
            runtime,
            leases=None if runtime.leases is None else dict(runtime.leases),
            stream_key_inflight=None if runtime.stream_key_inflight is None else dict(runtime.stream_key_inflight),
            overload_rejections=None if runtime.overload_rejections is None else list(runtime.overload_rejections),
            soft_overload_rejections=(
                None if runtime.soft_overload_rejections is None else list(runtime.soft_overload_rejections)
            ),
            outcome_buckets=(
                None
                if runtime.outcome_buckets is None
                else {bucket: list(counts) for bucket, counts in runtime.outcome_buckets.items()}
            ),
            ttft_samples=None if runtime.ttft_samples is None else list(runtime.ttft_samples),
            tps_samples=(
                None
                if runtime.tps_samples is None
                else {model: list(samples) for model, samples in runtime.tps_samples.items()}
            ),
            tps_weights=None if runtime.tps_weights is None else dict(runtime.tps_weights),
        )
        for lease in list((detached.leases or {}).values()):
            if now - lease.acquired_at >= stale_lease_ttl_seconds(lease.kind):
                apply_lease_release(detached, lease)
        snapshot[account_id] = detached
    return snapshot


@dataclass(frozen=True, slots=True)
class OpportunisticAdmissionRequest:
    model: str | None
    service_tier: str | None
    account_ids: Collection[str] | None
    prefer_earlier_reset_accounts: bool
    prefer_earlier_reset_window: ResetPreferenceWindow
    routing_strategy: RoutingStrategy
    budget_threshold_pct: float
    secondary_budget_threshold_pct: float
    lease_kind: AccountLeaseKind | None
    concurrency_caps: AccountConcurrencyCaps
    stream_reserve_slots: int
    record_account_cap_rejection: AccountCapRejectionCallback
    build_states: StatesBuilder
    # C2-2 routing/overload: dashboard snapshot resolved once by the caller.
    routing_tunables: RoutingTunables
    observe_only: bool = False
    # C2-3 resilience toggles: dashboard soft-drain value resolved by the
    # caller's snapshot; None inherits the env alias / default.
    soft_drain_enabled: bool | None = None


@dataclass(frozen=True, slots=True)
class OpportunisticAdmissionOutcome:
    account: Account | None
    error_message: str | None
    error_code: str | None = None
    resets_at: int | None = None


def _observe_selection_states(
    owner: OpportunisticAdmissionOwner,
    selection_inputs: SelectionInputsProtocol,
    *,
    build_states: StatesBuilder,
    routing_tunables: RoutingTunables,
    soft_drain_enabled: bool | None = None,
    model: str | None = None,
) -> tuple[list[AccountState], dict[str, Account]]:
    """Build the states ordinary selection would build, on a detached copy of the runtime.

    ``_state_from_account`` refreshes the usage-derived health tier (and bumps
    ``version``/``health_version``) on the runtime it is handed; a pure
    observation must not do that to the live balancer, nor reclaim leases or
    prune runtime entries there. The snapshot carries the same values with
    stale leases already expired, so the states — and therefore the selector's
    answer — are identical to a live build.
    """
    runtime_snapshot = owner._detached_runtime_snapshot(routing_tunables=routing_tunables)
    return build_states(
        accounts=selection_inputs.accounts,
        latest_primary=selection_inputs.latest_primary,
        latest_secondary=selection_inputs.latest_secondary,
        latest_monthly=selection_inputs.latest_monthly,
        standard_latest_primary=selection_inputs.standard_latest_primary,
        standard_latest_secondary=selection_inputs.standard_latest_secondary,
        runtime=runtime_snapshot,
        now=owner._clock.time(),
        routing_policy_override=selection_inputs.routing_policy_override,
        ignore_standard_quota_account_ids=selection_inputs.ignore_standard_quota_account_ids,
        encryptor=owner._encryptor,
        routing_tunables=routing_tunables,
        soft_drain_enabled=soft_drain_enabled,
        model=model,
        # The snapshot's cohort weight is discarded with it; logging from here
        # would repeat the transition on the next live build.
        log_weight_transitions=False,
    )


def _account_cap_closed(
    request: OpportunisticAdmissionRequest,
    states: list[AccountState],
) -> tuple[list[AccountState], OpportunisticAdmissionOutcome | None]:
    lease_kind = request.lease_kind
    selection_states = _filter_states_for_account_caps(
        states,
        lease_kind=lease_kind,
        caps=request.concurrency_caps,
        stream_reserve_slots=request.stream_reserve_slots,
    )
    if selection_states or not states:
        return selection_states, None
    logger.warning(
        "Account cap exhausted during opportunistic admission lease_kind=%s reason=%s candidates=%s",
        lease_kind,
        _account_cap_error_code(lease_kind),
        len(states),
    )
    request.record_account_cap_rejection(lease_kind)
    return selection_states, OpportunisticAdmissionOutcome(
        account=None,
        error_message="opportunistic burn window closed: no account capacity available",
        error_code=OPPORTUNISTIC_BURN_WINDOW_CLOSED,
    )


async def run_opportunistic_admission(
    owner: OpportunisticAdmissionOwner,
    *,
    request: OpportunisticAdmissionRequest,
) -> OpportunisticAdmissionOutcome:
    selection_inputs = await owner._load_selection_inputs(
        model=request.model,
        service_tier=request.service_tier,
        account_ids=request.account_ids,
    )
    if selection_inputs.error_code is not None and not selection_inputs.accounts:
        return OpportunisticAdmissionOutcome(
            account=None,
            error_message=selection_inputs.error_message,
            error_code=selection_inputs.error_code,
        )
    if request.observe_only:
        states, account_map = _observe_selection_states(
            owner,
            selection_inputs,
            build_states=request.build_states,
            routing_tunables=request.routing_tunables,
            soft_drain_enabled=request.soft_drain_enabled,
            model=request.model,
        )
        selection_states, cap_closed = _account_cap_closed(request, states)
        if cap_closed is not None:
            return cap_closed
    else:
        async with owner._runtime_lock:
            states, account_map = owner._prepare_sticky_selection_states(
                selection_inputs,
                required_account_id=None,
                redact_sensitive_details=False,
                routing_tunables=request.routing_tunables,
                soft_drain_enabled=request.soft_drain_enabled,
                model=request.model,
            )
            selection_states, cap_closed = _account_cap_closed(request, states)
            if cap_closed is not None:
                return cap_closed
    result = _select_account_preferring_budget_safe(
        selection_states,
        prefer_earlier_reset=request.prefer_earlier_reset_accounts,
        prefer_earlier_reset_window=request.prefer_earlier_reset_window,
        routing_strategy=request.routing_strategy,
        budget_threshold_pct=request.budget_threshold_pct,
        secondary_budget_threshold_pct=request.secondary_budget_threshold_pct,
        apply_secondary_budget_threshold=True,
        deterministic_probe=True,
        traffic_class=TRAFFIC_CLASS_OPPORTUNISTIC,
        ignore_standard_quota=False,
        usage_exhaustion_states=states,
    )
    if result.account is None:
        if result.error_code == USAGE_LIMIT_REACHED:
            return OpportunisticAdmissionOutcome(
                account=None,
                error_message=result.error_message,
                error_code=result.error_code,
                resets_at=result.resets_at,
            )
        return OpportunisticAdmissionOutcome(
            account=None,
            error_message=result.error_message,
            error_code=result.error_code or OPPORTUNISTIC_BURN_WINDOW_CLOSED,
        )
    account = account_map.get(result.account.account_id)
    if account is None:
        return OpportunisticAdmissionOutcome(
            account=None,
            error_message=result.error_message or "opportunistic burn window closed: no account available",
            error_code=OPPORTUNISTIC_BURN_WINDOW_CLOSED,
        )
    return OpportunisticAdmissionOutcome(account=_clone_account(account), error_message=None, error_code=None)
