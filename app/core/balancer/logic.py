from __future__ import annotations

import hashlib
import logging
import random
import time
from dataclasses import dataclass
from typing import Collection, Iterable, Literal

from app.core.balancer.types import FailureClass, UpstreamError
from app.core.usage import PLAN_CAPACITY_CREDITS_SECONDARY
from app.core.utils.retry import backoff_seconds, parse_retry_after
from app.db.models import AccountStatus

PERMANENT_FAILURE_CODES = {
    "refresh_token_expired": "Refresh token expired - re-login required",
    "refresh_token_reused": "Refresh token was reused - re-login required",
    "refresh_token_invalidated": "Refresh token was revoked - re-login required",
    "invalid_grant": "Refresh token grant invalid - re-login required",
    "token_invalidated": "Authentication token invalidated - re-login required",
    # ``token_expired`` from the OAuth refresh endpoint means the refresh
    # request itself failed because the refresh token (or the session it
    # belonged to) is no longer usable -- access-token-only expiry would have
    # returned a fresh token pair instead. Treat it as a permanent failure so
    # the account stops being routed to until it is re-authenticated.
    "token_expired": "Authentication token expired - re-login required",
    "account_session_expired": "ChatGPT session ended - re-login required",
    "account_auth_invalidated": "Authentication failed after token refresh - re-login required",
    "account_deactivated": "Account has been deactivated",
    "account_suspended": "Account has been suspended",
    "account_deleted": "Account has been deleted",
}

REAUTH_REQUIRED_FAILURE_CODES = frozenset(
    {
        "refresh_token_expired",
        "refresh_token_reused",
        "refresh_token_invalidated",
        "invalid_grant",
        "token_invalidated",
        "token_expired",
        "account_session_expired",
        "account_auth_invalidated",
    }
)

SECONDS_PER_DAY = 60 * 60 * 24
SECONDS_PER_HOUR = 60 * 60
SECONDS_PER_WEEK = 7 * SECONDS_PER_DAY
UNKNOWN_RESET_BUCKET_DAYS = 10_000
UNKNOWN_RESET_FALLBACK_SECONDS = 7 * SECONDS_PER_DAY
RELATIVE_AVAILABILITY_MIN_DIVISOR_SECONDS = 5 * 60
RELATIVE_AVAILABILITY_MIN_WEIGHT_FRACTION = 0.1
DEFAULT_RELATIVE_AVAILABILITY_POWER = 2.0
DEFAULT_RELATIVE_AVAILABILITY_TOP_K = 5
RoutingStrategy = Literal[
    "usage_weighted",
    "round_robin",
    "capacity_weighted",
    "relative_availability",
    "fill_first",
    "sequential_drain",
    "reset_drain",
    "single_account",
    "ordered_fallback",
]
TrafficClass = Literal["foreground", "opportunistic"]
UsageWeightedOrder = Literal["secondary_first", "primary_first"]
ResetPreferenceWindow = Literal["primary", "secondary"]
UNKNOWN_PLAN_FALLBACK = "free"
CAPACITY_PLAN_ALIASES = {
    "education": "edu",
    "k12": "edu",
    "guest": "free",
    "go": "free",
    "free_workspace": "free",
    "quorum": "free",
    "unknown": "free",
}

HEALTH_TIER_HEALTHY = 0
HEALTH_TIER_DRAINING = 1
HEALTH_TIER_PROBING = 2

DRAIN_PRIMARY_THRESHOLD_PCT = 85.0
DRAIN_SECONDARY_THRESHOLD_PCT = 90.0
DRAIN_ERROR_WINDOW_SECONDS = 60.0
DRAIN_ERROR_COUNT_THRESHOLD = 2
PROBE_QUIET_SECONDS = 60.0
PROBE_SUCCESS_STREAK_REQUIRED = 3
ROUTING_POLICY_NORMAL = "normal"
ROUTING_POLICY_BURN_FIRST = "burn_first"
ROUTING_POLICY_PRESERVE = "preserve"
TRAFFIC_CLASS_FOREGROUND = "foreground"
TRAFFIC_CLASS_OPPORTUNISTIC = "opportunistic"
PRESERVE_MIN_WEEKLY_FLOOR_PCT = 5.0
PRESERVE_MIN_SHORT_WINDOW_FLOOR_PCT = 10.0
NORMAL_LAST_ACCOUNT_EMERGENCY_FLOOR_PCT = 5.0
RECENT_FOREGROUND_ACTIVITY_SECONDS = 30 * 60

logger = logging.getLogger(__name__)

_RELATIVE_AVAILABILITY_LOG_PREFIX_CANDIDATE = "Relative availability candidate "
_RELATIVE_AVAILABILITY_LOG_PREFIX_TOP_K = "Relative availability top-k     "
_RELATIVE_AVAILABILITY_LOG_PREFIX_WINNER = "Relative availability winner    "


@dataclass
class AccountState:
    account_id: str
    status: AccountStatus
    used_percent: float | None = None
    reset_at: float | None = None
    primary_reset_at: int | None = None
    blocked_at: float | None = None
    cooldown_until: float | None = None
    secondary_used_percent: float | None = None
    secondary_reset_at: int | None = None
    last_error_at: float | None = None
    last_selected_at: float | None = None
    error_count: int = 0
    deactivation_reason: str | None = None
    plan_type: str | None = None
    capacity_credits: float | None = None
    health_tier: int = 0
    priority_used_percent: float | None = None
    priority_secondary_used_percent: float | None = None
    priority_reset_at: int | None = None
    priority_capacity_credits: float | None = None
    limit_scoped_usage: bool = False
    inflight_response_creates: int = 0
    inflight_streams: int = 0
    leased_tokens: float = 0.0
    routing_policy: str = ROUTING_POLICY_NORMAL
    ignore_standard_quota: bool = False


@dataclass
class SelectionResult:
    account: AccountState | None
    error_message: str | None


@dataclass(frozen=True, slots=True)
class RoutingCost:
    """Request-scoped planner cost applied after hard eligibility filters."""

    total: float = 0.0
    reason: str | None = None


RoutingCostsByAccount = dict[str, RoutingCost]


def _planner_cost(state: AccountState, routing_costs: RoutingCostsByAccount | None) -> float:
    if not routing_costs:
        return 0.0
    cost = routing_costs.get(state.account_id)
    if cost is None:
        return 0.0
    return float(cost.total)


def _usage_sort_key(state: AccountState) -> tuple[float, float, float, str]:
    primary_used = _priority_primary_used(state)
    secondary_used = _priority_secondary_used(state, primary_used)
    last_selected = state.last_selected_at or 0.0
    return secondary_used, primary_used, last_selected, state.account_id


def _primary_usage_sort_key(state: AccountState) -> tuple[float, float, float, str]:
    primary_used = _priority_primary_used(state)
    secondary_used = _priority_secondary_used(state, primary_used)
    last_selected = state.last_selected_at or 0.0
    return primary_used, secondary_used, last_selected, state.account_id


def _routing_policy(state: AccountState) -> str:
    if state.routing_policy in {
        ROUTING_POLICY_BURN_FIRST,
        ROUTING_POLICY_NORMAL,
        ROUTING_POLICY_PRESERVE,
    }:
        return state.routing_policy
    return ROUTING_POLICY_NORMAL


def _used_pct(state: AccountState, *, secondary: bool) -> float | None:
    if secondary:
        return state.secondary_used_percent if state.secondary_used_percent is not None else state.used_percent
    return state.used_percent


def _remaining_pct(state: AccountState, *, secondary: bool) -> float | None:
    used_pct = _used_pct(state, secondary=secondary)
    if used_pct is None:
        return None
    return max(0.0, 100.0 - min(100.0, used_pct))


def _seconds_until(reset_at: int | float | None, current: float) -> float | None:
    if reset_at is None:
        return None
    return max(0.0, float(reset_at) - current)


def _recent_foreground_activity(state: AccountState, current: float) -> bool:
    return state.last_selected_at is not None and current - state.last_selected_at <= RECENT_FOREGROUND_ACTIVITY_SECONDS


def _weekly_pace_floor_pct(state: AccountState, current: float) -> float:
    remaining_seconds = _seconds_until(state.secondary_reset_at, current)
    used_pct = _used_pct(state, secondary=True)
    if remaining_seconds is None or used_pct is None:
        return 100.0

    elapsed_seconds = max(0.0, SECONDS_PER_WEEK - remaining_seconds)
    expected_used_pct = min(100.0, (elapsed_seconds / SECONDS_PER_WEEK) * 100.0)
    behind_pace = used_pct + 5.0 < expected_used_pct

    if remaining_seconds <= 6 * SECONDS_PER_HOUR and behind_pace:
        pace_floor = 0.0
    elif remaining_seconds <= SECONDS_PER_DAY and behind_pace:
        pace_floor = 2.0
    elif behind_pace:
        pace_floor = 5.0
    else:
        pace_floor = 15.0

    if _recent_foreground_activity(state, current):
        pace_floor = max(pace_floor, 25.0)

    return max(PRESERVE_MIN_WEEKLY_FLOOR_PCT, pace_floor)


def _short_window_floor_pct(state: AccountState, current: float, *, preserve_count: int) -> float:
    remaining_seconds = _seconds_until(state.reset_at, current)
    floor = PRESERVE_MIN_SHORT_WINDOW_FLOOR_PCT
    if remaining_seconds is None:
        return 100.0
    if remaining_seconds > SECONDS_PER_HOUR:
        floor = max(floor, 20.0)
    if _recent_foreground_activity(state, current):
        floor = max(floor, 30.0)
    if preserve_count <= 1:
        floor = max(floor, 25.0)
    return floor


def _preserve_allows_opportunistic_burn(state: AccountState, current: float, *, preserve_count: int) -> bool:
    if _remaining_pct(state, secondary=True) is None or _remaining_pct(state, secondary=False) is None:
        return False
    if state.secondary_reset_at is None or state.reset_at is None:
        return False
    weekly_floor = _weekly_pace_floor_pct(state, current)
    short_floor = _short_window_floor_pct(state, current, preserve_count=preserve_count)
    return (_remaining_pct(state, secondary=True) or 0.0) > weekly_floor and (
        _remaining_pct(state, secondary=False) or 0.0
    ) > short_floor


def _has_other_usable_foreground_capacity(
    candidate: AccountState,
    available: list[AccountState],
    current: float,
) -> bool:
    preserve_count = sum(1 for state in available if _routing_policy(state) == ROUTING_POLICY_PRESERVE)
    for other in available:
        if other.account_id == candidate.account_id:
            continue
        if other.status != AccountStatus.ACTIVE:
            continue
        if _routing_policy(other) == ROUTING_POLICY_PRESERVE:
            if _preserve_allows_opportunistic_burn(other, current, preserve_count=preserve_count):
                return True
            continue
        return True
    return False


def _above_emergency_floor(state: AccountState) -> bool:
    primary_remaining = _remaining_pct(state, secondary=False)
    secondary_remaining = _remaining_pct(state, secondary=True)
    if primary_remaining is None or secondary_remaining is None:
        return False
    return (
        primary_remaining > NORMAL_LAST_ACCOUNT_EMERGENCY_FLOOR_PCT
        and secondary_remaining > NORMAL_LAST_ACCOUNT_EMERGENCY_FLOOR_PCT
    )


def _filter_opportunistic_candidates(
    available: list[AccountState],
    current: float,
) -> tuple[list[AccountState], str | None]:
    burn_first: list[AccountState] = []
    normal: list[AccountState] = []
    preserve: list[AccountState] = []
    preserve_count = sum(1 for state in available if _routing_policy(state) == ROUTING_POLICY_PRESERVE)

    for state in available:
        policy = _routing_policy(state)
        if policy == ROUTING_POLICY_BURN_FIRST:
            if _has_other_usable_foreground_capacity(state, available, current) or _above_emergency_floor(state):
                burn_first.append(state)
        elif policy == ROUTING_POLICY_PRESERVE:
            if _preserve_allows_opportunistic_burn(state, current, preserve_count=preserve_count):
                preserve.append(state)
        else:
            if _has_other_usable_foreground_capacity(state, available, current) or _above_emergency_floor(state):
                normal.append(state)

    if burn_first or normal or preserve:
        return [*burn_first, *normal, *preserve], None

    if any(_routing_policy(state) == ROUTING_POLICY_PRESERVE for state in available):
        return [], "preserve floor or stale usage data blocks opportunistic burn"
    return [], "no expendable account has emergency foreground reserve"


def _reset_preference_bucket(state: AccountState, current: float, window: ResetPreferenceWindow) -> int:
    if window == "primary":
        reset_at = state.primary_reset_at
        if reset_at is None:
            reset_at = state.priority_reset_at if state.priority_reset_at is not None else state.secondary_reset_at
    else:
        reset_at = state.priority_reset_at if state.priority_reset_at is not None else state.secondary_reset_at
        if reset_at is None:
            reset_at = state.primary_reset_at
    if reset_at is None:
        return UNKNOWN_RESET_BUCKET_DAYS * SECONDS_PER_DAY
    remaining_seconds = max(0, int(reset_at - current))
    if window == "secondary":
        return remaining_seconds // SECONDS_PER_DAY
    return remaining_seconds


def _prefer_earlier_reset_candidates(
    available: list[AccountState],
    current: float,
    window: ResetPreferenceWindow,
) -> list[AccountState]:
    earliest_bucket = min(_reset_preference_bucket(state, current, window) for state in available)
    return [state for state in available if _reset_preference_bucket(state, current, window) == earliest_bucket]


def _fallback_secondary_capacity_credits(plan_type: str | None) -> float:
    normalized = (plan_type or "").strip().lower()
    resolved_plan = CAPACITY_PLAN_ALIASES.get(normalized, normalized or UNKNOWN_PLAN_FALLBACK)
    return PLAN_CAPACITY_CREDITS_SECONDARY.get(
        resolved_plan,
        PLAN_CAPACITY_CREDITS_SECONDARY[UNKNOWN_PLAN_FALLBACK],
    )


def select_account(
    states: Iterable[AccountState],
    now: float | None = None,
    *,
    prefer_earlier_reset: bool = False,
    prefer_earlier_reset_window: ResetPreferenceWindow = "secondary",
    routing_strategy: RoutingStrategy = "capacity_weighted",
    allow_backoff_fallback: bool = True,
    deterministic_probe: bool = False,
    relative_availability_power: float = DEFAULT_RELATIVE_AVAILABILITY_POWER,
    relative_availability_top_k: int = DEFAULT_RELATIVE_AVAILABILITY_TOP_K,
    usage_weighted_order: UsageWeightedOrder = "secondary_first",
    traffic_class: TrafficClass = TRAFFIC_CLASS_FOREGROUND,
    ignore_standard_quota: bool = False,
    bypass_quota_exceeded: bool = False,
    bypass_quota_exceeded_account_ids: Collection[str] | None = None,
    primary_first_usage_weighted: bool = False,
    routing_costs: RoutingCostsByAccount | None = None,
    ordered_account_ids: Collection[str] | None = None,
) -> SelectionResult:
    """Select an eligible account by applying availability checks and routing strategy.

    This function filters out accounts that cannot currently serve traffic
    (for example paused, deactivated, still rate-limited, or in active
    cooldown), attempts controlled recovery from transient error backoff,
    and then chooses a candidate using the configured balancing strategy.

    Args:
        states: Candidate account states to evaluate for the current request.
        now: Unix timestamp in seconds used as the evaluation clock. If
            ``None``, the current system time is used.
        prefer_earlier_reset: Whether to bias selection toward accounts whose
            configured quota window resets sooner.
        prefer_earlier_reset_window: Quota window used for reset preference
            (``"primary"`` for short window or ``"secondary"`` for weekly),
            with the other window used as fallback when the configured reset is
            unavailable.
        routing_strategy: Balancing strategy used to pick from the effective
            pool (``"capacity_weighted"``, ``"sequential_drain"``,
            ``"reset_drain"``, ``"single_account"``, ``"relative_availability"``,
            ``"fill_first"``, ``"round_robin"``, or ``"usage_weighted"``).
        allow_backoff_fallback: Whether to allow a fallback attempt with the
            backoff account nearest to recovery when no fully available
            account exists.
        deterministic_probe: Whether weighted strategies should use a
            deterministic probe order instead of random weighted choice.
        relative_availability_power: Exponent applied to normalized relative
            availability weights.
        relative_availability_top_k: Maximum number of highest-weight
            relative-availability candidates retained before weighted draw.
        usage_weighted_order: Whether usage-weighted routing ranks secondary
            window pressure first, or primary-window pressure first for
            budget-safe fallback selection.
        traffic_class: Whether the request is normal foreground traffic or
            opportunistic traffic that may only use explicitly expendable
            account capacity.
        ignore_standard_quota: Whether to ignore the account's standard
            primary/secondary quota status. This is only for models that are
            gated by a separate additional quota pool.
        bypass_quota_exceeded: Backward-compatible alias for ignoring standard
            quota status for gated quota selection.
        bypass_quota_exceeded_account_ids: Optional narrower account-id scope
            for the same bypass.
        primary_first_usage_weighted: Whether usage-weighted routing should
            rank by primary-window pressure before secondary-window pressure.
        routing_costs: Optional request-scoped planner costs. Lower cost wins
            after hard eligibility, health tier, and reset-bucket filtering.

    Returns:
        A ``SelectionResult`` containing the selected ``AccountState`` and no
        error message when routing can proceed, or ``None`` plus a
        human-readable error message when no account is eligible.
    """
    current = now or time.time()
    available: list[AccountState] = []
    in_error_backoff: list[AccountState] = []
    all_states = list(states)
    bypass_account_ids = None if bypass_quota_exceeded_account_ids is None else set(bypass_quota_exceeded_account_ids)

    for state in all_states:
        bypass_standard_quota = (
            ignore_standard_quota
            or state.ignore_standard_quota
            or bypass_quota_exceeded
            or (bypass_account_ids is not None and state.account_id in bypass_account_ids)
        )
        if state.status in (AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED):
            continue
        if state.status == AccountStatus.PAUSED:
            continue
        if state.status == AccountStatus.RATE_LIMITED:
            if state.reset_at and current >= state.reset_at:
                state.status = AccountStatus.ACTIVE
                state.used_percent = 0.0
                state.error_count = 0
                state.reset_at = None
            elif not bypass_standard_quota:
                continue
        if state.status == AccountStatus.QUOTA_EXCEEDED:
            if state.reset_at and current >= state.reset_at:
                state.status = AccountStatus.ACTIVE
                state.used_percent = 0.0
                state.secondary_used_percent = 0.0
                state.reset_at = None
            elif not bypass_standard_quota:
                continue
        if state.cooldown_until and current >= state.cooldown_until:
            state.cooldown_until = None
            state.last_error_at = None
            state.error_count = 0
        if state.cooldown_until and current < state.cooldown_until:
            continue
        if state.error_count >= 3:
            backoff = min(300, 30 * (2 ** (state.error_count - 3)))
            if state.last_error_at and current - state.last_error_at < backoff:
                in_error_backoff.append(state)
                continue
            # Error backoff expired — reset error state so recovery is
            # not penalised by stale counts. The account has already
            # been held back for the full backoff period; letting it
            # re-enter the pool with a clean slate avoids the problem
            # where a previously-high error_count causes an immediate
            # return to maximum backoff on the very next transient error.
            state.error_count = 0
            state.last_error_at = None
        available.append(state)

    if traffic_class == TRAFFIC_CLASS_OPPORTUNISTIC and available:
        opportunistic_available, reason = _filter_opportunistic_candidates(available, current)
        if not opportunistic_available:
            return SelectionResult(None, f"opportunistic burn window closed: {reason}")
        available = opportunistic_available

    if not available:
        in_error_backoff_ids = {state.account_id for state in in_error_backoff}
        hard_blocked_exists = any(
            state.status
            in (
                AccountStatus.PAUSED,
                AccountStatus.REAUTH_REQUIRED,
                AccountStatus.DEACTIVATED,
                AccountStatus.RATE_LIMITED,
                AccountStatus.QUOTA_EXCEEDED,
            )
            and state.account_id not in in_error_backoff_ids
            for state in all_states
        )
        if allow_backoff_fallback and (len(in_error_backoff) > 1 or (in_error_backoff and hard_blocked_exists)):

            def _backoff_expires_at(s: AccountState) -> float:
                backoff = min(300, 30 * (2 ** (s.error_count - 3)))
                return (s.last_error_at or 0.0) + backoff

            available.append(min(in_error_backoff, key=_backoff_expires_at))
            if traffic_class == TRAFFIC_CLASS_OPPORTUNISTIC:
                opportunistic_available, reason = _filter_opportunistic_candidates(available, current)
                if not opportunistic_available:
                    return SelectionResult(None, f"opportunistic burn window closed: {reason}")
                available = opportunistic_available
        else:
            reauth_required = [s for s in all_states if s.status == AccountStatus.REAUTH_REQUIRED]
            deactivated = [s for s in all_states if s.status == AccountStatus.DEACTIVATED]
            paused = [s for s in all_states if s.status == AccountStatus.PAUSED]
            rate_limited = [s for s in all_states if s.status == AccountStatus.RATE_LIMITED]
            quota_exceeded = [s for s in all_states if s.status == AccountStatus.QUOTA_EXCEEDED]

            if not rate_limited and not quota_exceeded:
                if paused and reauth_required and deactivated:
                    return SelectionResult(None, "All accounts are paused, deactivated, or require re-authentication")
                if paused and reauth_required:
                    return SelectionResult(None, "All accounts are paused or require re-authentication")
                if paused and deactivated:
                    return SelectionResult(None, "All accounts are paused or deactivated")
                if reauth_required and deactivated:
                    return SelectionResult(None, "All accounts are deactivated or require re-authentication")
                if paused:
                    return SelectionResult(None, "All accounts are paused")
                if reauth_required:
                    return SelectionResult(None, "All accounts require re-authentication")
                if deactivated:
                    return SelectionResult(None, "All accounts are deactivated")
            if quota_exceeded:
                reset_candidates = [s.reset_at for s in quota_exceeded if s.reset_at]
                if reset_candidates:
                    wait_seconds = max(0, min(reset_candidates) - int(current))
                    return SelectionResult(None, _format_retry_hint(wait_seconds))
            cooldowns = [s.cooldown_until for s in all_states if s.cooldown_until and s.cooldown_until > current]
            if cooldowns:
                wait_seconds = max(0.0, min(cooldowns) - current)
                return SelectionResult(None, _format_retry_hint(wait_seconds))
            return SelectionResult(None, "No available accounts")

    def _reset_first_sort_key(state: AccountState) -> tuple[int, float, float, float, float, str]:
        reset_bucket_days = _reset_preference_bucket(state, current, prefer_earlier_reset_window)
        secondary_used, primary_used, last_selected, account_id = _usage_sort_key(state)
        return (
            reset_bucket_days,
            _planner_cost(state, routing_costs),
            secondary_used,
            primary_used,
            last_selected,
            account_id,
        )

    def _primary_reset_first_sort_key(state: AccountState) -> tuple[int, float, float, float, float, str]:
        reset_bucket_days = _reset_preference_bucket(state, current, prefer_earlier_reset_window)
        primary_used, secondary_used, last_selected, account_id = _primary_usage_sort_key(state)
        return (
            reset_bucket_days,
            _planner_cost(state, routing_costs),
            primary_used,
            secondary_used,
            last_selected,
            account_id,
        )

    def _primary_usage_sort_key_with_cost(state: AccountState) -> tuple[float, float, float, float, str]:
        primary_used, secondary_used, last_selected, account_id = _primary_usage_sort_key(state)
        return _planner_cost(state, routing_costs), primary_used, secondary_used, last_selected, account_id

    def _usage_sort_key_with_cost(state: AccountState) -> tuple[float, float, float, float, str]:
        secondary_used, primary_used, last_selected, account_id = _usage_sort_key(state)
        return _planner_cost(state, routing_costs), secondary_used, primary_used, last_selected, account_id

    def _round_robin_sort_key(state: AccountState) -> tuple[float, float, str]:
        # Pick the least recently selected account, then stabilize by account_id.
        return _planner_cost(state, routing_costs), state.last_selected_at or 0.0, state.account_id

    if routing_strategy == "single_account":
        candidate_pool = [state for state in available if not _usage_exhausted(state)]
        if not candidate_pool:
            return SelectionResult(None, "Selected account is exhausted or unavailable")
        selected = min(candidate_pool, key=lambda state: state.account_id)
        return SelectionResult(selected, None)

    if routing_strategy == "ordered_fallback":
        ordered_pool = _ordered_fallback_pool(available, ordered_account_ids)
        if not ordered_pool:
            return SelectionResult(None, "No ordered fallback accounts are available")
        return SelectionResult(ordered_pool[0], None)

    if routing_strategy == "sequential_drain":
        candidate_pool = [state for state in available if not _usage_exhausted(state)]
        if not candidate_pool:
            return SelectionResult(None, "No available accounts")
        selected = min(candidate_pool, key=_sequential_drain_sort_key)
        return SelectionResult(selected, None)

    if routing_strategy == "reset_drain":
        candidate_pool = [state for state in available if not _usage_exhausted(state)]
        if not candidate_pool:
            return SelectionResult(None, "No available accounts")
        selected = min(candidate_pool, key=lambda state: _reset_drain_sort_key(state, current))
        return SelectionResult(selected, None)

    healthy = [s for s in available if s.health_tier == HEALTH_TIER_HEALTHY]
    probing = [s for s in available if s.health_tier == HEALTH_TIER_PROBING]
    draining = [s for s in available if s.health_tier == HEALTH_TIER_DRAINING]
    health_pool = healthy or probing or draining or available
    burn_first = [s for s in health_pool if _routing_policy(s) == ROUTING_POLICY_BURN_FIRST]
    normal = [s for s in health_pool if _routing_policy(s) == ROUTING_POLICY_NORMAL]
    preserve = [s for s in health_pool if _routing_policy(s) == ROUTING_POLICY_PRESERVE]
    effective_pool = burn_first or normal or preserve or health_pool
    effective_prefer_earlier_reset = prefer_earlier_reset and routing_strategy != "relative_availability"

    if routing_strategy == "round_robin":
        selected = min(effective_pool, key=_round_robin_sort_key)
    elif routing_strategy == "capacity_weighted":
        candidate_pool = (
            _prefer_earlier_reset_candidates(effective_pool, current, prefer_earlier_reset_window)
            if effective_prefer_earlier_reset
            else effective_pool
        )
        if deterministic_probe:
            selected = min(candidate_pool, key=lambda state: _capacity_probe_sort_key_with_cost(state, routing_costs))
        else:
            candidate_pool = _lowest_planner_cost_candidates(candidate_pool, routing_costs)
            selected = _select_capacity_weighted(candidate_pool)
    elif routing_strategy == "relative_availability":
        candidate_pool = _lowest_planner_cost_candidates(effective_pool, routing_costs)
        selected = _select_relative_availability(
            candidate_pool,
            current=current,
            power=relative_availability_power,
            top_k=relative_availability_top_k,
            deterministic_probe=deterministic_probe,
        )
    elif routing_strategy == "fill_first":
        candidate_pool = (
            _prefer_earlier_reset_candidates(effective_pool, current, prefer_earlier_reset_window)
            if prefer_earlier_reset
            else effective_pool
        )
        selected = _select_fill_first(candidate_pool)
    else:
        effective_usage_weighted_order: UsageWeightedOrder = (
            "primary_first" if primary_first_usage_weighted else usage_weighted_order
        )
        if effective_usage_weighted_order == "primary_first":
            selected = min(
                effective_pool,
                key=(
                    _primary_reset_first_sort_key
                    if effective_prefer_earlier_reset
                    else _primary_usage_sort_key_with_cost
                ),
            )
        else:
            selected = min(
                effective_pool,
                key=_reset_first_sort_key if effective_prefer_earlier_reset else _usage_sort_key_with_cost,
            )
    return SelectionResult(selected, None)


def _ordered_fallback_pool(
    available: Iterable[AccountState],
    ordered_account_ids: Collection[str] | None,
) -> list[AccountState]:
    order = _normalized_ordered_account_ids(ordered_account_ids)
    if not order:
        return []
    available_by_id = {
        state.account_id: state for state in available if state.account_id in order and not _usage_exhausted(state)
    }
    return [available_by_id[account_id] for account_id in order if account_id in available_by_id]


def _normalized_ordered_account_ids(ordered_account_ids: Collection[str] | None) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw_account_id in ordered_account_ids or ():
        account_id = raw_account_id.strip()
        if not account_id or account_id in seen:
            continue
        seen.add(account_id)
        ordered.append(account_id)
    return tuple(ordered)


def _remaining_secondary_credits(state: AccountState) -> float:
    """Return remaining absolute credits for the secondary (7-day) window."""
    capacity = (
        state.priority_capacity_credits if state.priority_capacity_credits is not None else state.capacity_credits
    )
    if capacity is None:
        capacity = _fallback_secondary_capacity_credits(state.plan_type)
    elif capacity <= 0:
        return 0.0
    primary_used = _priority_primary_used(state)
    used_pct = _priority_secondary_used(state, primary_used)
    return max(0.0, capacity * (1.0 - min(used_pct, 100.0) / 100.0))


def _priority_primary_used(state: AccountState) -> float:
    value = state.priority_used_percent if state.priority_used_percent is not None else state.used_percent
    return value if value is not None else 0.0


def _priority_secondary_used(state: AccountState, primary_used: float | None = None) -> float:
    if state.limit_scoped_usage and state.priority_secondary_used_percent is None:
        return primary_used if primary_used is not None else _priority_primary_used(state)
    value = (
        state.priority_secondary_used_percent
        if state.priority_secondary_used_percent is not None
        else state.secondary_used_percent
    )
    if value is not None:
        return value
    return primary_used if primary_used is not None else _priority_primary_used(state)


def _capacity_probe_sort_key(state: AccountState) -> tuple[float, float, float, float, str]:
    secondary_used, primary_used, last_selected, account_id = _usage_sort_key(state)
    return (-_remaining_secondary_credits(state), secondary_used, primary_used, last_selected, account_id)


def _relative_availability_divisor_seconds(state: AccountState, current: float) -> float:
    reset_at = state.priority_reset_at if state.priority_reset_at is not None else state.secondary_reset_at
    if reset_at is None:
        remaining_seconds = float(UNKNOWN_RESET_FALLBACK_SECONDS)
    else:
        remaining_seconds = max(0.0, float(reset_at) - current)
    return max(remaining_seconds, float(RELATIVE_AVAILABILITY_MIN_DIVISOR_SECONDS))


def _relative_availability_remaining_seconds(state: AccountState, current: float) -> float:
    reset_at = state.priority_reset_at if state.priority_reset_at is not None else state.secondary_reset_at
    if reset_at is None:
        return float(UNKNOWN_RESET_FALLBACK_SECONDS)
    return max(0.0, float(reset_at) - current)


def _relative_availability_raw_score(state: AccountState, current: float) -> float:
    remaining_credits = _remaining_secondary_credits(state)
    if remaining_credits <= 0.0:
        return 0.0
    return remaining_credits / _relative_availability_divisor_seconds(state, current)


def _relative_availability_label(state: AccountState) -> str:
    return state.account_id


def _relative_availability_score_per_minute(raw_score: float) -> float:
    return raw_score * 60.0


def _log_relative_availability_candidate_scores(
    raw_scores: list[tuple[AccountState, float]],
    *,
    current: float,
) -> None:
    for state, raw_score in raw_scores:
        remaining_seconds = _relative_availability_remaining_seconds(state, current)
        logger.debug(
            (
                f"{_RELATIVE_AVAILABILITY_LOG_PREFIX_CANDIDATE}account=%s "
                "remaining_credits=%.2f remaining_minutes=%.2f score_per_minute=%.6f"
            ),
            _relative_availability_label(state),
            _remaining_secondary_credits(state),
            remaining_seconds / 60.0,
            _relative_availability_score_per_minute(raw_score),
        )


def _log_relative_availability_top_k(
    weighted_candidates: list[tuple[AccountState, float, float]],
    *,
    current: float,
) -> None:
    formatted_candidates = ", ".join(
        (
            f"account={_relative_availability_label(state)} "
            f"remaining_credits={_remaining_secondary_credits(state):.2f} "
            f"remaining_minutes={_relative_availability_remaining_seconds(state, current) / 60.0:.2f} "
            f"score_per_minute={_relative_availability_score_per_minute(raw_score):.6f} "
            f"weight={weight:.8f}"
        )
        for state, weight, raw_score in weighted_candidates
    )
    logger.info("%s%s", _RELATIVE_AVAILABILITY_LOG_PREFIX_TOP_K, formatted_candidates)


def _relative_availability_weighted_candidates(
    available: list[AccountState],
    *,
    current: float,
    power: float,
    top_k: int,
) -> list[tuple[AccountState, float, float]]:
    raw_scores = [(state, _relative_availability_raw_score(state, current)) for state in available]
    _log_relative_availability_candidate_scores(raw_scores, current=current)
    best_raw_score = max((score for _, score in raw_scores), default=0.0)
    if best_raw_score <= 0.0:
        return []

    weighted: list[tuple[AccountState, float, float]] = []
    safe_power = power if power > 0.0 else DEFAULT_RELATIVE_AVAILABILITY_POWER
    for state, raw_score in raw_scores:
        normalized_score = raw_score / best_raw_score
        weight = normalized_score**safe_power
        if weight < RELATIVE_AVAILABILITY_MIN_WEIGHT_FRACTION:
            continue
        weighted.append((state, weight, raw_score))

    if not weighted:
        return []

    weighted.sort(
        key=lambda item: (
            -item[1],
            -item[2],
            *_usage_sort_key(item[0]),
        )
    )
    safe_top_k = max(1, top_k)
    top_candidates = weighted[:safe_top_k]
    _log_relative_availability_top_k(top_candidates, current=current)
    return top_candidates


def _log_relative_availability_winner(
    winner: AccountState,
    *,
    current: float,
    weight: float | None,
    raw_score: float,
) -> None:
    remaining_seconds = _relative_availability_remaining_seconds(winner, current)
    logger.info(
        (
            f"{_RELATIVE_AVAILABILITY_LOG_PREFIX_WINNER}account=%s "
            "remaining_credits=%.2f remaining_minutes=%.2f score_per_minute=%.6f weight=%s"
        ),
        _relative_availability_label(winner),
        _remaining_secondary_credits(winner),
        remaining_seconds / 60.0,
        _relative_availability_score_per_minute(raw_score),
        f"{weight:.8f}" if weight is not None else "fallback",
    )


def _select_relative_availability(
    available: list[AccountState],
    *,
    current: float,
    power: float,
    top_k: int,
    deterministic_probe: bool,
) -> AccountState:
    weighted_candidates = _relative_availability_weighted_candidates(
        available,
        current=current,
        power=power,
        top_k=top_k,
    )
    if not weighted_candidates:
        winner = min(available, key=_usage_sort_key)
        _log_relative_availability_winner(
            winner,
            current=current,
            weight=None,
            raw_score=_relative_availability_raw_score(winner, current),
        )
        return winner
    if deterministic_probe:
        winner, weight, raw_score = weighted_candidates[0]
        _log_relative_availability_winner(winner, current=current, weight=weight, raw_score=raw_score)
        return winner
    states = [state for state, _, _ in weighted_candidates]
    weights = [weight for _, weight, _ in weighted_candidates]
    total = sum(weights)
    if total <= 0.0:
        winner = min(available, key=_usage_sort_key)
        _log_relative_availability_winner(
            winner,
            current=current,
            weight=None,
            raw_score=_relative_availability_raw_score(winner, current),
        )
        return winner
    winner = random.choices(states, weights=weights, k=1)[0]
    for state, weight, raw_score in weighted_candidates:
        if state.account_id == winner.account_id:
            _log_relative_availability_winner(winner, current=current, weight=weight, raw_score=raw_score)
            break
    return winner


def _stable_tie_breaker(account_id: str) -> str:
    return hashlib.sha256(account_id.encode("utf-8")).hexdigest()


def _configured_capacity_credits(state: AccountState) -> float:
    if state.capacity_credits is not None and state.capacity_credits > 0:
        return state.capacity_credits
    return _fallback_secondary_capacity_credits(state.plan_type)


def _usage_exhausted(state: AccountState) -> bool:
    primary_used = state.used_percent if state.used_percent is not None else 0.0
    secondary_used = state.secondary_used_percent if state.secondary_used_percent is not None else primary_used
    return primary_used >= 100.0 or secondary_used >= 100.0


def _sequential_drain_sort_key(state: AccountState) -> tuple[float, str, str]:
    return (
        _configured_capacity_credits(state),
        _stable_tie_breaker(state.account_id),
        state.account_id,
    )


def _weekly_reset_timestamp(state: AccountState, current: float) -> float:
    if state.secondary_reset_at is not None and float(state.secondary_reset_at) > current:
        return float(state.secondary_reset_at)
    if state.reset_at is not None and float(state.reset_at) > current:
        return float(state.reset_at)
    return float("inf")


def _reset_drain_sort_key(state: AccountState, current: float) -> tuple[int, float, float, float, str, str]:
    primary_remaining = 100.0 - (state.used_percent if state.used_percent is not None else 0.0)
    secondary_remaining = 100.0 - (
        state.secondary_used_percent
        if state.secondary_used_percent is not None
        else state.used_percent
        if state.used_percent is not None
        else 0.0
    )
    reset_at = _weekly_reset_timestamp(state, current)
    reset_bucket_days = (
        UNKNOWN_RESET_BUCKET_DAYS if reset_at == float("inf") else max(0, int((reset_at - current) // SECONDS_PER_DAY))
    )
    return (
        reset_bucket_days,
        -max(0.0, primary_remaining),
        -max(0.0, secondary_remaining),
        reset_at,
        _stable_tie_breaker(state.account_id),
        state.account_id,
    )


def _capacity_probe_sort_key_with_cost(
    state: AccountState,
    routing_costs: RoutingCostsByAccount | None,
) -> tuple[float, float, float, float, float, str]:
    secondary_used, primary_used, last_selected, account_id = _usage_sort_key(state)
    return (
        _planner_cost(state, routing_costs),
        -_remaining_secondary_credits(state),
        secondary_used,
        primary_used,
        last_selected,
        account_id,
    )


def _lowest_planner_cost_candidates(
    available: list[AccountState],
    routing_costs: RoutingCostsByAccount | None,
) -> list[AccountState]:
    if not routing_costs:
        return available
    lowest_cost = min(_planner_cost(state, routing_costs) for state in available)
    return [state for state in available if _planner_cost(state, routing_costs) == lowest_cost]


def _select_capacity_weighted(available: list[AccountState]) -> AccountState:
    """Select an account with probability proportional to remaining secondary credits."""
    weights = [_remaining_secondary_credits(s) for s in available]
    total = sum(weights)
    if total <= 0.0:
        # All accounts exhausted — fall back to deterministic usage-weighted
        return min(available, key=_usage_sort_key)
    return random.choices(available, weights=weights, k=1)[0]


def _fill_first_sort_key(state: AccountState) -> tuple[float, float, str]:
    primary_used = state.used_percent if state.used_percent is not None else 0.0
    secondary_used = state.secondary_used_percent if state.secondary_used_percent is not None else 0.0
    return -primary_used, -secondary_used, state.account_id


def _select_fill_first(available: list[AccountState]) -> AccountState:
    """Pick the eligible account with the highest primary 5h ``used_percent``.

    Deterministic. ``None`` ``used_percent`` is treated as ``0.0`` so a
    freshly refreshed account ties with an unknown-usage account.

    When two or more candidates share the same primary ``used_percent``,
    the account with the **higher** secondary (weekly) ``used_percent`` is
    preferred — i.e. the one with the least remaining weekly capacity.
    This drains the most-saturated account first and preserves the freshest
    one for later cycles, matching operator intent for "fill first" behavior.
    ``account_id`` ascending is the final stable tiebreaker.

    Drained accounts are only reachable here when no healthy or probing
    account exists, via the existing ``effective_pool`` ladder; this helper
    introduces no new bypass.
    """
    return min(available, key=_fill_first_sort_key)


def handle_rate_limit(state: AccountState, error: UpstreamError) -> None:
    state.status = AccountStatus.RATE_LIMITED
    state.error_count += 1
    state.last_error_at = time.time()
    state.blocked_at = time.time()

    reset_at = _extract_reset_at(error)
    if reset_at is not None:
        state.reset_at = reset_at

    message = error.get("message")
    delay = parse_retry_after(message) if message else None
    if delay is None:
        delay = backoff_seconds(state.error_count)
    state.cooldown_until = time.time() + delay


QUOTA_EXCEEDED_COOLDOWN_SECONDS = 120.0

# Upper bound for the user-visible "Try again in {N}s" hint that
# ``select_account`` surfaces when zero candidates are selectable. The clamp
# protects clients from waiting the worst-case persisted ``reset_at`` after
# OpenAI-side reset events that propagate lazily through ``/wham/usage`` (see
# https://github.com/Soju06/codex-lb/issues/676). codex-lb's background usage
# refresh runs every ``usage_refresh_interval_seconds`` (default 60s) and the
# per-status cooldowns are 120s, so a 300s ceiling lets clients reattempt
# inside the auto-recovery window. The underlying ``AccountState.reset_at``
# and ``AccountState.cooldown_until`` fields are not clamped.
SELECTOR_RETRY_HINT_MAX_SECONDS = 300


def _format_retry_hint(wait_seconds: float) -> str:
    capped = min(max(0.0, wait_seconds), float(SELECTOR_RETRY_HINT_MAX_SECONDS))
    return f"Rate limit exceeded. Try again in {capped:.0f}s"


def handle_quota_exceeded(state: AccountState, error: UpstreamError) -> None:
    state.status = AccountStatus.QUOTA_EXCEEDED
    state.used_percent = 100.0
    state.blocked_at = time.time()
    state.cooldown_until = time.time() + QUOTA_EXCEEDED_COOLDOWN_SECONDS

    reset_at = _extract_reset_at(error)
    if reset_at is not None:
        state.reset_at = reset_at
    else:
        state.reset_at = int(time.time() + 3600)


def handle_permanent_failure(state: AccountState, error_code: str) -> None:
    state.status = account_status_for_permanent_failure(error_code)
    state.deactivation_reason = PERMANENT_FAILURE_CODES.get(
        error_code,
        f"Authentication failed: {error_code}",
    )
    state.blocked_at = None


def account_status_for_permanent_failure(error_code: str) -> AccountStatus:
    if error_code in REAUTH_REQUIRED_FAILURE_CODES:
        return AccountStatus.REAUTH_REQUIRED
    return AccountStatus.DEACTIVATED


FailoverAction = Literal["failover_next", "surface"]


def failover_decision(
    *,
    failure_class: FailureClass,
    downstream_visible: bool,
    candidates_remaining: int,
) -> FailoverAction:
    if downstream_visible:
        return "surface"
    if candidates_remaining <= 0:
        return "surface"
    if failure_class in ("rate_limit", "quota", "retryable_transient"):
        return "failover_next"
    return "surface"


def _extract_reset_at(error: UpstreamError) -> int | None:
    reset_at = error.get("resets_at")
    if reset_at is not None:
        return int(reset_at)
    reset_in = error.get("resets_in_seconds")
    if reset_in is not None:
        return int(time.time() + float(reset_in))
    return None


def evaluate_health_tier(
    state: AccountState,
    *,
    now: float | None = None,
    drain_entered_at: float | None = None,
    probe_success_streak: int = 0,
    drain_primary_threshold_pct: float = DRAIN_PRIMARY_THRESHOLD_PCT,
    drain_secondary_threshold_pct: float = DRAIN_SECONDARY_THRESHOLD_PCT,
    drain_error_window_seconds: float = DRAIN_ERROR_WINDOW_SECONDS,
    drain_error_count_threshold: int = DRAIN_ERROR_COUNT_THRESHOLD,
    probe_quiet_seconds: float = PROBE_QUIET_SECONDS,
    probe_success_streak_required: int = PROBE_SUCCESS_STREAK_REQUIRED,
) -> int:
    current = now or time.time()

    if state.status in (
        AccountStatus.RATE_LIMITED,
        AccountStatus.QUOTA_EXCEEDED,
        AccountStatus.PAUSED,
        AccountStatus.REAUTH_REQUIRED,
        AccountStatus.DEACTIVATED,
    ):
        return state.health_tier

    should_drain = False

    if state.used_percent is not None and state.used_percent >= drain_primary_threshold_pct:
        should_drain = True

    if state.secondary_used_percent is not None and state.secondary_used_percent >= drain_secondary_threshold_pct:
        should_drain = True

    if (
        state.error_count >= drain_error_count_threshold
        and state.last_error_at is not None
        and current - state.last_error_at < drain_error_window_seconds
    ):
        should_drain = True

    current_tier = state.health_tier

    if current_tier == HEALTH_TIER_HEALTHY:
        return HEALTH_TIER_DRAINING if should_drain else HEALTH_TIER_HEALTHY

    if current_tier == HEALTH_TIER_DRAINING:
        if should_drain:
            return HEALTH_TIER_DRAINING
        if drain_entered_at is not None and current - drain_entered_at >= probe_quiet_seconds:
            return HEALTH_TIER_PROBING
        return HEALTH_TIER_DRAINING

    if current_tier == HEALTH_TIER_PROBING:
        if should_drain:
            return HEALTH_TIER_DRAINING
        if probe_success_streak >= probe_success_streak_required:
            return HEALTH_TIER_HEALTHY
        return HEALTH_TIER_PROBING

    return HEALTH_TIER_HEALTHY
