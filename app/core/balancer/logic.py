from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

from app.core.balancer.types import UpstreamError
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


@dataclass
class AccountState:
    account_id: str
    status: AccountStatus
    used_percent: float | None = None
    reset_at: float | None = None
    secondary_used_percent: float | None = None
    secondary_reset_at: int | None = None
    last_error_at: float | None = None
    last_selected_at: float | None = None
    error_count: int = 0
    deactivation_reason: str | None = None


@dataclass
class SelectionResult:
    account: AccountState | None
    error_message: str | None


def select_account(states: Iterable[AccountState], now: float | None = None) -> SelectionResult:
    current = now or time.time()
    available: list[AccountState] = []
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
        if state.error_count >= 3:
            backoff = min(300, 30 * (2 ** (state.error_count - 3)))
            if state.last_error_at and current - state.last_error_at < backoff:
                continue
        available.append(state)

    if not available:
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
        return SelectionResult(None, "No available accounts")

    def _sort_key(state: AccountState) -> tuple[int, float, float, float, str]:
        reset_bucket_days = UNKNOWN_RESET_BUCKET_DAYS
        if state.secondary_reset_at is not None:
            reset_bucket_days = max(
                0,
                int((state.secondary_reset_at - current) // SECONDS_PER_DAY),
            )

        primary_used = state.used_percent if state.used_percent is not None else 0.0
        secondary_used = (
            state.secondary_used_percent if state.secondary_used_percent is not None else primary_used
        )
        last_selected = state.last_selected_at or 0.0

        return reset_bucket_days, secondary_used, primary_used, last_selected, state.account_id

    selected = min(available, key=_sort_key)
    return SelectionResult(selected, None)


def handle_rate_limit(state: AccountState, error: UpstreamError) -> None:
    state.status = AccountStatus.RATE_LIMITED
    state.error_count += 1
    state.last_error_at = time.time()
    message = error.get("message")
    delay = parse_retry_after(message) if message else None
    if delay is None:
        delay = backoff_seconds(state.error_count)
    state.reset_at = time.time() + delay


def handle_quota_exceeded(state: AccountState, error: UpstreamError) -> None:
    state.status = AccountStatus.QUOTA_EXCEEDED
    state.used_percent = 100.0

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
