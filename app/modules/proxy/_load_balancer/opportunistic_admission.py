"""Opportunistic admission check — private implementation unit of ``LoadBalancer``.

``LoadBalancer.check_opportunistic_admission`` delegates here. The check asks
the deterministic opportunistic selector the question a real request would
ask — same model, same account scope, local account caps only for the
requested ``lease_kind`` — and reports the answer without acting
on it: no lease is acquired and no health, sticky or selection state is
written. The runtime lock is held only for the balancer's ordinary state
preparation (stale-lease reclaim, runtime prune, state build), exactly as
ordinary selection does, and is released before the selector runs.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Protocol

from app.core.balancer import (
    TRAFFIC_CLASS_OPPORTUNISTIC,
    USAGE_LIMIT_REACHED,
    AccountState,
    ResetPreferenceWindow,
    RoutingStrategy,
)
from app.db.models import Account
from app.modules.proxy._load_balancer.sticky_selection import (
    SelectionInputsProtocol,
    _account_cap_error_code,
    _clone_account,
    _filter_states_for_account_caps,
    _select_account_preferring_budget_safe,
)
from app.modules.proxy._load_balancer.types import AccountConcurrencyCaps, AccountLeaseKind

# Preserve the established observability surface while implementation moves to
# a private module; operators and tests filter this logger by its public owner.
logger = logging.getLogger("app.modules.proxy.load_balancer")

OPPORTUNISTIC_BURN_WINDOW_CLOSED = "opportunistic_burn_window_closed"

AccountCapRejectionCallback = Callable[[AccountLeaseKind | None], None]


class OpportunisticAdmissionOwner(Protocol):
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
    ) -> tuple[list[AccountState], dict[str, Account]]: ...


@dataclass(frozen=True, slots=True)
class OpportunisticAdmissionRequest:
    model: str | None
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


@dataclass(frozen=True, slots=True)
class OpportunisticAdmissionOutcome:
    account: Account | None
    error_message: str | None
    error_code: str | None = None
    resets_at: int | None = None


async def run_opportunistic_admission(
    owner: OpportunisticAdmissionOwner,
    *,
    request: OpportunisticAdmissionRequest,
) -> OpportunisticAdmissionOutcome:
    selection_inputs = await owner._load_selection_inputs(
        model=request.model,
        account_ids=request.account_ids,
    )
    if selection_inputs.error_code is not None and not selection_inputs.accounts:
        return OpportunisticAdmissionOutcome(
            account=None,
            error_message=selection_inputs.error_message,
            error_code=selection_inputs.error_code,
        )
    lease_kind = request.lease_kind
    async with owner._runtime_lock:
        states, account_map = owner._prepare_sticky_selection_states(
            selection_inputs,
            required_account_id=None,
            redact_sensitive_details=False,
        )
        selection_states = _filter_states_for_account_caps(
            states,
            lease_kind=lease_kind,
            caps=request.concurrency_caps,
            stream_reserve_slots=request.stream_reserve_slots,
        )
        if not selection_states and states:
            logger.warning(
                "Account cap exhausted during opportunistic admission lease_kind=%s reason=%s candidates=%s",
                lease_kind,
                _account_cap_error_code(lease_kind),
                len(states),
            )
            request.record_account_cap_rejection(lease_kind)
            return OpportunisticAdmissionOutcome(
                account=None,
                error_message="opportunistic burn window closed: no account capacity available",
                error_code=OPPORTUNISTIC_BURN_WINDOW_CLOSED,
            )
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
            error_code=OPPORTUNISTIC_BURN_WINDOW_CLOSED,
        )
    account = account_map.get(result.account.account_id)
    if account is None:
        return OpportunisticAdmissionOutcome(
            account=None,
            error_message=result.error_message or "opportunistic burn window closed: no account available",
            error_code=OPPORTUNISTIC_BURN_WINDOW_CLOSED,
        )
    return OpportunisticAdmissionOutcome(account=_clone_account(account), error_message=None, error_code=None)
