from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Iterable, Literal

from app.core.balancer.types import UpstreamError
from app.core.usage import PLAN_CAPACITY_CREDITS_SECONDARY
from app.core.utils.retry import backoff_seconds, parse_retry_after
from app.db.models import AccountStatus

PERMANENT_FAILURE_CODES = {
    "refresh_token_expired": "Refresh token expired - re-login required",
    "refresh_token_reused": "Refresh token was reused - re-login required",
    "refresh_token_invalidated": "Refresh token was revoked - re-login required",
    "account_suspended": "Account has been suspended",
    "account_deleted": "Account has been deleted",
}

SECONDS_PER_DAY = 60 * 60 * 24
UNKNOWN_RESET_BUCKET_DAYS = 10_000
RoutingStrategy = Literal["usage_weighted", "round_robin", "capacity_weighted"]
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


@dataclass
class AccountState:
    account_id: str
    status: AccountStatus
    used_percent: float | None = None
    reset_at: float | None = None
    cooldown_until: float | None = None
    secondary_used_percent: float | None = None
    secondary_reset_at: int | None = None
    last_error_at: float | None = None
    last_selected_at: float | None = None
    error_count: int = 0
    deactivation_reason: str | None = None
    plan_type: str | None = None
    capacity_credits: float | None = None


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
) -> SelectionResult:
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
        # If any account is in error backoff, try the one closest to
        # backoff expiry — it may have recovered.  Hard-blocked accounts
        # (paused/deactivated/rate-limited/quota-exceeded) can't serve
        # traffic regardless, so they shouldn't prevent trying recoverable
        # accounts.  This prevents #140: all accounts locked out during
        # a widespread upstream outage.
        if len(in_error_backoff) > 1 and allow_backoff_fallback:

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

    if routing_strategy == "round_robin":
        selected = min(available, key=_round_robin_sort_key)
    elif routing_strategy == "capacity_weighted":
        candidate_pool = _prefer_earlier_reset_candidates(available, current) if prefer_earlier_reset else available
        selected = _select_capacity_weighted(candidate_pool)
    else:
        selected = min(available, key=_reset_first_sort_key if prefer_earlier_reset else _usage_sort_key)
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


def _extract_reset_at(error: UpstreamError) -> int | None:
    reset_at = error.get("resets_at")
    if reset_at is not None:
        return int(reset_at)
    reset_in = error.get("resets_in_seconds")
    if reset_in is not None:
        return int(time.time() + float(reset_in))
    return None
