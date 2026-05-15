from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Iterable, Literal

from app.core.balancer.types import FailureClass, UpstreamError
from app.core.usage import PLAN_CAPACITY_CREDITS_SECONDARY
from app.core.utils.retry import backoff_seconds, parse_retry_after
from app.db.models import AccountStatus

PERMANENT_FAILURE_CODES = {
    "refresh_token_expired": "Refresh token expired - re-login required",
    "refresh_token_reused": "Refresh token was reused - re-login required",
    "refresh_token_invalidated": "Refresh token was revoked - re-login required",
    # ``token_expired`` from the OAuth refresh endpoint means the refresh
    # request itself failed because the refresh token (or the session it
    # belonged to) is no longer usable -- access-token-only expiry would have
    # returned a fresh token pair instead. Treat it as a permanent failure so
    # the account stops being routed to until it is re-authenticated.
    "token_expired": "Authentication token expired - re-login required",
    "account_deactivated": "Account has been deactivated",
    "account_suspended": "Account has been suspended",
    "account_deleted": "Account has been deleted",
}

SECONDS_PER_DAY = 60 * 60 * 24
SECONDS_PER_HOUR = 60 * 60
SECONDS_PER_WEEK = 7 * SECONDS_PER_DAY
UNKNOWN_RESET_BUCKET_DAYS = 10_000
RoutingStrategy = Literal["usage_weighted", "round_robin", "capacity_weighted"]
TrafficClass = Literal["foreground", "opportunistic"]
UsageWeightedOrder = Literal["secondary_first", "primary_first"]
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


@dataclass
class AccountState:
    account_id: str
    status: AccountStatus
    used_percent: float | None = None
    reset_at: float | None = None
    primary_reset_at: int | float | None = None
    blocked_at: float | None = None
    cooldown_until: float | None = None
    secondary_used_percent: float | None = None
    secondary_reset_at: int | None = None
    primary_usage_fresh: bool = False
    secondary_usage_fresh: bool = False
    last_error_at: float | None = None
    last_selected_at: float | None = None
    last_foreground_selected_at: float | None = None
    error_count: int = 0
    deactivation_reason: str | None = None
    plan_type: str | None = None
    capacity_credits: float | None = None
    health_tier: int = 0
    routing_policy: str = ROUTING_POLICY_NORMAL


@dataclass
class SelectionResult:
    account: AccountState | None
    error_message: str | None


def _usage_sort_key(state: AccountState) -> tuple[float, float, float, str]:
    primary_used = state.used_percent if state.used_percent is not None else 0.0
    secondary_used = state.secondary_used_percent if state.secondary_used_percent is not None else primary_used
    last_selected = state.last_selected_at or 0.0
    return secondary_used, primary_used, last_selected, state.account_id


def _primary_usage_sort_key(state: AccountState) -> tuple[float, float, float, str]:
    primary_used = state.used_percent if state.used_percent is not None else 0.0
    secondary_used = state.secondary_used_percent if state.secondary_used_percent is not None else primary_used
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
    return (
        state.last_foreground_selected_at is not None
        and current - state.last_foreground_selected_at <= RECENT_FOREGROUND_ACTIVITY_SECONDS
    )


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


def _primary_window_reset_at(state: AccountState) -> int | float | None:
    return state.primary_reset_at if state.primary_reset_at is not None else state.reset_at


def _short_window_floor_pct(state: AccountState, current: float, *, preserve_count: int) -> float:
    remaining_seconds = _seconds_until(_primary_window_reset_at(state), current)
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
    if not state.primary_usage_fresh or not state.secondary_usage_fresh:
        return False
    if _remaining_pct(state, secondary=True) is None or _remaining_pct(state, secondary=False) is None:
        return False
    if state.secondary_reset_at is None or _primary_window_reset_at(state) is None:
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
    *,
    ignore_standard_quota: bool = False,
) -> bool:
    preserve_count = sum(1 for state in available if _routing_policy(state) == ROUTING_POLICY_PRESERVE)
    for other in available:
        if other.account_id == candidate.account_id:
            continue
        if other.status != AccountStatus.ACTIVE and not ignore_standard_quota:
            continue
        if _routing_policy(other) == ROUTING_POLICY_PRESERVE:
            if _preserve_allows_opportunistic_burn(
                other,
                current,
                preserve_count=preserve_count,
            ):
                return True
            continue
        if _above_emergency_floor(other):
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
    *,
    ignore_standard_quota: bool = False,
) -> tuple[list[AccountState], str | None]:
    burn_first: list[AccountState] = []
    normal: list[AccountState] = []
    preserve: list[AccountState] = []
    preserve_count = sum(1 for state in available if _routing_policy(state) == ROUTING_POLICY_PRESERVE)

    for state in available:
        policy = _routing_policy(state)
        if policy == ROUTING_POLICY_BURN_FIRST:
            if _has_other_usable_foreground_capacity(
                state,
                available,
                current,
                ignore_standard_quota=ignore_standard_quota,
            ) or _above_emergency_floor(state):
                burn_first.append(state)
        elif policy == ROUTING_POLICY_PRESERVE:
            if _preserve_allows_opportunistic_burn(state, current, preserve_count=preserve_count):
                preserve.append(state)
        else:
            if _has_other_usable_foreground_capacity(
                state,
                available,
                current,
                ignore_standard_quota=ignore_standard_quota,
            ) or _above_emergency_floor(state):
                normal.append(state)

    if burn_first or normal or preserve:
        return [*burn_first, *normal, *preserve], None

    if any(_routing_policy(state) == ROUTING_POLICY_PRESERVE for state in available):
        return [], "preserve floor or stale usage data blocks opportunistic burn"
    return [], "no expendable account has emergency foreground reserve"


def _reset_bucket_days(state: AccountState, current: float) -> int:
    if state.secondary_reset_at is None:
        return UNKNOWN_RESET_BUCKET_DAYS
    return max(0, int((state.secondary_reset_at - current) // SECONDS_PER_DAY))


def _prefer_earlier_reset_candidates(available: list[AccountState], current: float) -> list[AccountState]:
    earliest_bucket = min(_reset_bucket_days(state, current) for state in available)
    return [state for state in available if _reset_bucket_days(state, current) == earliest_bucket]


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
    routing_strategy: RoutingStrategy = "capacity_weighted",
    allow_backoff_fallback: bool = True,
    deterministic_probe: bool = False,
    usage_weighted_order: UsageWeightedOrder = "secondary_first",
    traffic_class: TrafficClass = TRAFFIC_CLASS_FOREGROUND,
    ignore_standard_quota: bool = False,
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
            secondary quota window resets sooner.
        routing_strategy: Balancing strategy used to pick from the effective
            pool (``"capacity_weighted"``, ``"round_robin"``, or
            ``"usage_weighted"``).
        allow_backoff_fallback: Whether to allow a fallback attempt with the
            backoff account nearest to recovery when no fully available
            account exists.
        deterministic_probe: Whether capacity-weighted routing should use a
            deterministic probe order instead of random weighted choice.
        usage_weighted_order: Whether usage-weighted routing ranks secondary
            window pressure first, or primary-window pressure first for
            budget-safe fallback selection.
        traffic_class: Whether the request is normal foreground traffic or
            opportunistic traffic that may only use explicitly expendable
            account capacity.
        ignore_standard_quota: Whether to ignore the account's standard
            primary/secondary quota status. This is only for models that are
            gated by a separate additional quota pool.

    Returns:
        A ``SelectionResult`` containing the selected ``AccountState`` and no
        error message when routing can proceed, or ``None`` plus a
        human-readable error message when no account is eligible.
    """
    current = now or time.time()
    available: list[AccountState] = []
    in_error_backoff: list[AccountState] = []
    all_states = list(states)

    for state in all_states:
        if state.status == AccountStatus.DEACTIVATED:
            continue
        if state.status == AccountStatus.PAUSED:
            continue
        if state.status == AccountStatus.RATE_LIMITED:
            if state.reset_at and current >= state.reset_at:
                state.status = AccountStatus.ACTIVE
                state.error_count = 0
                state.reset_at = None
            elif not ignore_standard_quota:
                continue
        if state.status == AccountStatus.QUOTA_EXCEEDED:
            if state.reset_at and current >= state.reset_at:
                state.status = AccountStatus.ACTIVE
                state.used_percent = 0.0
                state.reset_at = None
            elif not ignore_standard_quota:
                continue
        standard_quota_status_ignored = ignore_standard_quota and state.status == AccountStatus.QUOTA_EXCEEDED
        if state.cooldown_until and current >= state.cooldown_until:
            state.cooldown_until = None
            state.last_error_at = None
            state.error_count = 0
        if state.cooldown_until and current < state.cooldown_until and not standard_quota_status_ignored:
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
        opportunistic_available, reason = _filter_opportunistic_candidates(
            available,
            current,
            ignore_standard_quota=ignore_standard_quota,
        )
        if not opportunistic_available:
            return SelectionResult(None, f"opportunistic burn window closed: {reason}")
        available = opportunistic_available

    if not available:
        hard_blocked_exists = any(
            state.status
            in (
                AccountStatus.PAUSED,
                AccountStatus.DEACTIVATED,
                AccountStatus.RATE_LIMITED,
                AccountStatus.QUOTA_EXCEEDED,
            )
            for state in all_states
        )
        if allow_backoff_fallback and (len(in_error_backoff) > 1 or (in_error_backoff and hard_blocked_exists)):

            def _backoff_expires_at(s: AccountState) -> float:
                backoff = min(300, 30 * (2 ** (s.error_count - 3)))
                return (s.last_error_at or 0.0) + backoff

            available.append(min(in_error_backoff, key=_backoff_expires_at))
        else:
            deactivated = [s for s in all_states if s.status == AccountStatus.DEACTIVATED]
            paused = [s for s in all_states if s.status == AccountStatus.PAUSED]
            rate_limited = [s for s in all_states if s.status == AccountStatus.RATE_LIMITED]
            quota_exceeded = [s for s in all_states if s.status == AccountStatus.QUOTA_EXCEEDED]

            if paused and deactivated and not rate_limited and not quota_exceeded:
                return SelectionResult(None, "All accounts are paused or require re-authentication")
            if paused and not rate_limited and not quota_exceeded:
                return SelectionResult(None, "All accounts are paused")
            if deactivated and not rate_limited and not quota_exceeded:
                return SelectionResult(None, "All accounts require re-authentication")
            if quota_exceeded:
                reset_candidates = [s.reset_at for s in quota_exceeded if s.reset_at]
                if reset_candidates:
                    wait_seconds = max(0, min(reset_candidates) - int(current))
                    return SelectionResult(None, f"Rate limit exceeded. Try again in {wait_seconds:.0f}s")
            cooldowns = [s.cooldown_until for s in all_states if s.cooldown_until and s.cooldown_until > current]
            if cooldowns:
                wait_seconds = max(0.0, min(cooldowns) - current)
                return SelectionResult(None, f"Rate limit exceeded. Try again in {wait_seconds:.0f}s")
            return SelectionResult(None, "No available accounts")

    def _reset_first_sort_key(state: AccountState) -> tuple[int, float, float, float, str]:
        reset_bucket_days = _reset_bucket_days(state, current)
        secondary_used, primary_used, last_selected, account_id = _usage_sort_key(state)
        return reset_bucket_days, secondary_used, primary_used, last_selected, account_id

    def _round_robin_sort_key(state: AccountState) -> tuple[float, str]:
        # Pick the least recently selected account, then stabilize by account_id.
        return state.last_selected_at or 0.0, state.account_id

    burn_first = [s for s in available if _routing_policy(s) == ROUTING_POLICY_BURN_FIRST]
    normal = [s for s in available if _routing_policy(s) == ROUTING_POLICY_NORMAL]
    preserve = [s for s in available if _routing_policy(s) == ROUTING_POLICY_PRESERVE]
    policy_pool = burn_first or normal or preserve or available

    healthy = [s for s in policy_pool if s.health_tier == HEALTH_TIER_HEALTHY]
    probing = [s for s in policy_pool if s.health_tier == HEALTH_TIER_PROBING]
    draining = [s for s in policy_pool if s.health_tier == HEALTH_TIER_DRAINING]
    effective_pool = healthy or probing or draining or policy_pool

    if routing_strategy == "round_robin":
        selected = min(effective_pool, key=_round_robin_sort_key)
    elif routing_strategy == "capacity_weighted":
        candidate_pool = (
            _prefer_earlier_reset_candidates(effective_pool, current) if prefer_earlier_reset else effective_pool
        )
        if deterministic_probe:
            selected = min(candidate_pool, key=_capacity_probe_sort_key)
        else:
            selected = _select_capacity_weighted(candidate_pool)
    else:
        if usage_weighted_order == "primary_first":
            selected = min(effective_pool, key=_primary_usage_sort_key)
        else:
            selected = min(effective_pool, key=_reset_first_sort_key if prefer_earlier_reset else _usage_sort_key)
    return SelectionResult(selected, None)


def _remaining_secondary_credits(state: AccountState) -> float:
    """Return remaining absolute credits for the secondary (7-day) window."""
    capacity = state.capacity_credits
    if capacity is None:
        capacity = _fallback_secondary_capacity_credits(state.plan_type)
    elif capacity <= 0:
        return 0.0
    if state.secondary_used_percent is not None:
        used_pct = state.secondary_used_percent
    elif state.used_percent is not None:
        used_pct = state.used_percent
    else:
        used_pct = 0.0
    return max(0.0, capacity * (1.0 - min(used_pct, 100.0) / 100.0))


def _capacity_probe_sort_key(state: AccountState) -> tuple[float, float, float, float, str]:
    secondary_used, primary_used, last_selected, account_id = _usage_sort_key(state)
    return (-_remaining_secondary_credits(state), secondary_used, primary_used, last_selected, account_id)


def _select_capacity_weighted(available: list[AccountState]) -> AccountState:
    """Select an account with probability proportional to remaining secondary credits."""
    weights = [_remaining_secondary_credits(s) for s in available]
    total = sum(weights)
    if total <= 0.0:
        # All accounts exhausted — fall back to deterministic usage-weighted
        return min(available, key=_usage_sort_key)
    return random.choices(available, weights=weights, k=1)[0]


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
    state.status = AccountStatus.DEACTIVATED
    state.deactivation_reason = PERMANENT_FAILURE_CODES.get(
        error_code,
        f"Authentication failed: {error_code}",
    )
    state.blocked_at = None


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
