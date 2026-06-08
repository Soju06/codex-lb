from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

ProviderRoutingStrategy = Literal[
    "capacity_weighted",
    "round_robin",
    "sequential_drain",
    "reset_drain",
    "single_account",
    "ordered_fallback",
]

SECONDS_PER_DAY = 24 * 60 * 60
UNKNOWN_RESET_BUCKET_DAYS = 10_000


@dataclass(frozen=True, slots=True)
class ProviderQuotaWindow:
    dimension: str
    used: int
    limit: int | None
    reset_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ProviderAccountRoutingState:
    account_id: str
    status: str
    quota_windows: tuple[ProviderQuotaWindow, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ProviderRoutingSettings:
    strategy: ProviderRoutingStrategy = "capacity_weighted"
    single_account_id: str | None = None
    ordered_account_ids: tuple[str, ...] = field(default_factory=tuple)
    quota_threshold_pct: float = 100.0
    round_robin_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderSelectionResult:
    account_id: str | None
    denied_reason: str | None
    candidate_account_ids: tuple[str, ...]


def select_provider_account(
    states: list[ProviderAccountRoutingState],
    settings: ProviderRoutingSettings,
    *,
    now: datetime | None = None,
) -> ProviderSelectionResult:
    current = now or datetime.now(timezone.utc)
    active = [state for state in states if state.status == "active"]
    if not active:
        return ProviderSelectionResult(None, "no_active_provider_accounts", ())

    scoped = _scope_single_account(active, settings)
    if not scoped:
        return ProviderSelectionResult(None, "single_account_unavailable", ())

    budget_safe = [state for state in scoped if _within_budget(state, settings.quota_threshold_pct, current)]
    if not budget_safe:
        return ProviderSelectionResult(
            None,
            "provider_quota_budget_exhausted",
            tuple(state.account_id for state in scoped),
        )
    if settings.strategy == "ordered_fallback":
        if not settings.ordered_account_ids:
            return ProviderSelectionResult(
                None,
                "ordered_fallback_not_configured",
                tuple(state.account_id for state in budget_safe),
            )
        ordered_candidate_ids = {state.account_id for state in budget_safe}
        if not any(account_id in ordered_candidate_ids for account_id in settings.ordered_account_ids):
            return ProviderSelectionResult(
                None,
                "ordered_fallback_unavailable",
                tuple(state.account_id for state in budget_safe),
            )

    selected = _select_from_budget_safe(budget_safe, settings, current)
    return ProviderSelectionResult(
        selected.account_id,
        None,
        tuple(state.account_id for state in budget_safe),
    )


def _scope_single_account(
    states: list[ProviderAccountRoutingState],
    settings: ProviderRoutingSettings,
) -> list[ProviderAccountRoutingState]:
    if settings.strategy != "single_account":
        return states
    if not settings.single_account_id:
        return []
    return [state for state in states if state.account_id == settings.single_account_id]


def _select_from_budget_safe(
    states: list[ProviderAccountRoutingState],
    settings: ProviderRoutingSettings,
    now: datetime,
) -> ProviderAccountRoutingState:
    if settings.strategy == "ordered_fallback":
        ordered_state = _select_ordered_fallback(states, settings.ordered_account_ids)
        if ordered_state is not None:
            return ordered_state
    if settings.strategy == "round_robin":
        return min(
            states,
            key=lambda state: (_round_robin_after_cursor(state, settings.round_robin_cursor), state.account_id),
        )
    if settings.strategy == "sequential_drain":
        return min(
            states,
            key=lambda state: (_total_limit(state), _stable_tie_breaker(state.account_id), state.account_id),
        )
    if settings.strategy == "reset_drain":
        return min(states, key=lambda state: _reset_drain_key(state, now))
    if settings.strategy == "single_account":
        return states[0]
    return max(
        states,
        key=lambda state: (_remaining_capacity(state, now), _stable_tie_breaker(state.account_id), state.account_id),
    )


def _select_ordered_fallback(
    states: list[ProviderAccountRoutingState],
    ordered_account_ids: tuple[str, ...],
) -> ProviderAccountRoutingState | None:
    if not ordered_account_ids:
        return None
    by_id = {state.account_id: state for state in states}
    for account_id in ordered_account_ids:
        state = by_id.get(account_id)
        if state is not None:
            return state
    return None


def _within_budget(state: ProviderAccountRoutingState, threshold_pct: float, now: datetime) -> bool:
    if not state.quota_windows:
        return True
    for window in state.quota_windows:
        if window.limit is None or window.limit <= 0:
            continue
        used_pct = (_effective_used(window, now) / window.limit) * 100.0
        if used_pct >= threshold_pct:
            return False
    return True


def _remaining_capacity(state: ProviderAccountRoutingState, now: datetime | None = None) -> int:
    if not state.quota_windows:
        return 1
    current = now or datetime.now(timezone.utc)
    remaining = 0
    for window in state.quota_windows:
        if window.limit is None or window.limit <= 0:
            continue
        remaining += max(0, window.limit - _effective_used(window, current))
    return max(remaining, 1)


def _total_limit(state: ProviderAccountRoutingState) -> int:
    total = sum(max(0, window.limit or 0) for window in state.quota_windows)
    return total if total > 0 else 1


def _reset_drain_key(state: ProviderAccountRoutingState, now: datetime) -> tuple[int, int, float, str, str]:
    reset_at = _nearest_reset_at(state)
    reset_bucket_days = UNKNOWN_RESET_BUCKET_DAYS
    reset_timestamp = float("inf")
    if reset_at is not None:
        reset_timestamp = reset_at.timestamp()
        reset_bucket_days = max(0, int((reset_timestamp - now.timestamp()) // SECONDS_PER_DAY))
    return (
        reset_bucket_days,
        -_remaining_capacity(state, now),
        reset_timestamp,
        _stable_tie_breaker(state.account_id),
        state.account_id,
    )


def _nearest_reset_at(state: ProviderAccountRoutingState) -> datetime | None:
    known = [window.reset_at for window in state.quota_windows if window.reset_at is not None]
    return min(known) if known else None


def _effective_used(window: ProviderQuotaWindow, now: datetime) -> int:
    if window.reset_at is not None and _is_expired(window.reset_at, now):
        return 0
    return window.used


def _is_expired(reset_at: datetime, now: datetime) -> bool:
    if reset_at.tzinfo is None:
        return reset_at <= now.replace(tzinfo=None)
    return reset_at <= now


def _round_robin_after_cursor(state: ProviderAccountRoutingState, cursor: str | None) -> int:
    if cursor is None:
        return 0
    return 1 if state.account_id > cursor else 2


def _stable_tie_breaker(account_id: str) -> str:
    return hashlib.sha256(account_id.encode("utf-8")).hexdigest()
