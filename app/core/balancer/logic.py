from __future__ import annotations

import logging
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
    "account_deactivated": "Account has been deactivated",
    "account_suspended": "Account has been suspended",
    "account_deleted": "Account has been deleted",
}

SECONDS_PER_DAY = 60 * 60 * 24
UNKNOWN_RESET_BUCKET_DAYS = 10_000
UNKNOWN_RESET_FALLBACK_SECONDS = 7 * SECONDS_PER_DAY
RELATIVE_AVAILABILITY_MIN_DIVISOR_SECONDS = 5 * 60
RELATIVE_AVAILABILITY_MIN_WEIGHT_FRACTION = 0.1
DEFAULT_RELATIVE_AVAILABILITY_POWER = 2.0
DEFAULT_RELATIVE_AVAILABILITY_TOP_K = 5
RoutingStrategy = Literal["usage_weighted", "round_robin", "capacity_weighted", "relative_availability"]
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
    blocked_at: float | None = None
    cooldown_until: float | None = None
    secondary_used_percent: float | None = None
    secondary_reset_at: int | None = None
    last_error_at: float | None = None
    last_selected_at: float | None = None
    error_count: int = 0
    email: str | None = None
    deactivation_reason: str | None = None
    plan_type: str | None = None
    capacity_credits: float | None = None
    health_tier: int = 0


@dataclass
class SelectionResult:
    account: AccountState | None
    error_message: str | None


def _usage_sort_key(state: AccountState) -> tuple[float, float, float, str]:
    primary_used = state.used_percent if state.used_percent is not None else 0.0
    secondary_used = state.secondary_used_percent if state.secondary_used_percent is not None else primary_used
    last_selected = state.last_selected_at or 0.0
    return secondary_used, primary_used, last_selected, state.account_id


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
    relative_availability_power: float = DEFAULT_RELATIVE_AVAILABILITY_POWER,
    relative_availability_top_k: int = DEFAULT_RELATIVE_AVAILABILITY_TOP_K,
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
            pool (``"capacity_weighted"``, ``"round_robin"``,
            ``"relative_availability"``, or ``"usage_weighted"``).
        allow_backoff_fallback: Whether to allow a fallback attempt with the
            backoff account nearest to recovery when no fully available
            account exists.
        deterministic_probe: Whether weighted strategies should use a
            deterministic probe order instead of random weighted choice.
        relative_availability_power: Exponent applied to normalized relative
            availability weights.
        relative_availability_top_k: Maximum number of highest-weight
            relative-availability candidates retained before weighted draw.

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
            else:
                continue
        if state.status == AccountStatus.QUOTA_EXCEEDED:
            if state.reset_at and current >= state.reset_at:
                state.status = AccountStatus.ACTIVE
                state.used_percent = 0.0
                state.reset_at = None
            else:
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

    healthy = [s for s in available if s.health_tier == HEALTH_TIER_HEALTHY]
    probing = [s for s in available if s.health_tier == HEALTH_TIER_PROBING]
    draining = [s for s in available if s.health_tier == HEALTH_TIER_DRAINING]
    effective_pool = healthy or probing or draining or available
    effective_prefer_earlier_reset = prefer_earlier_reset and routing_strategy != "relative_availability"

    if routing_strategy == "round_robin":
        selected = min(effective_pool, key=_round_robin_sort_key)
    elif routing_strategy == "capacity_weighted":
        candidate_pool = (
            _prefer_earlier_reset_candidates(effective_pool, current)
            if effective_prefer_earlier_reset
            else effective_pool
        )
        if deterministic_probe:
            selected = min(candidate_pool, key=_capacity_probe_sort_key)
        else:
            selected = _select_capacity_weighted(candidate_pool)
    elif routing_strategy == "relative_availability":
        selected = _select_relative_availability(
            effective_pool,
            current=current,
            power=relative_availability_power,
            top_k=relative_availability_top_k,
            deterministic_probe=deterministic_probe,
        )
    else:
        selected = min(
            effective_pool,
            key=_reset_first_sort_key if effective_prefer_earlier_reset else _usage_sort_key,
        )
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


def _relative_availability_divisor_seconds(state: AccountState, current: float) -> float:
    if state.secondary_reset_at is None:
        remaining_seconds = float(UNKNOWN_RESET_FALLBACK_SECONDS)
    else:
        remaining_seconds = max(0.0, float(state.secondary_reset_at) - current)
    return max(remaining_seconds, float(RELATIVE_AVAILABILITY_MIN_DIVISOR_SECONDS))


def _relative_availability_remaining_seconds(state: AccountState, current: float) -> float:
    if state.secondary_reset_at is None:
        return float(UNKNOWN_RESET_FALLBACK_SECONDS)
    return max(0.0, float(state.secondary_reset_at) - current)


def _relative_availability_raw_score(state: AccountState, current: float) -> float:
    remaining_credits = _remaining_secondary_credits(state)
    if remaining_credits <= 0.0:
        return 0.0
    return remaining_credits / _relative_availability_divisor_seconds(state, current)


def _relative_availability_label(state: AccountState) -> str:
    return state.email or state.account_id


def _relative_availability_score_per_minute(raw_score: float) -> float:
    return raw_score * 60.0


def _log_relative_availability_candidate_scores(
    raw_scores: list[tuple[AccountState, float]],
    *,
    current: float,
) -> None:
    for state, raw_score in raw_scores:
        remaining_seconds = _relative_availability_remaining_seconds(state, current)
        logger.info(
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
