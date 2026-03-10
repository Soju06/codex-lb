from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.usage.depletion import (
    EWMAState,
    aggregate_risks,
    classify_risk,
    compute_burn_rate,
    compute_depletion_risk,
    compute_safe_usage_percent,
    ewma_update,
)
from app.core.utils.time import naive_utc_to_epoch, utcnow

# In-memory EWMA state: keyed by (account_id, limit_name, window)
# Persists across requests; resets on process restart.
_ewma_states: dict[tuple[str, str, str], EWMAState] = {}


@dataclass
class DepletionMetrics:
    risk: float
    risk_level: str  # "safe" | "warning" | "danger" | "critical"
    rate_per_second: float
    burn_rate: float
    safe_usage_percent: float  # budget line position
    projected_exhaustion_at: datetime | None
    seconds_until_exhaustion: float | None


@dataclass
class AggregateDepletionMetrics:
    risk: float
    risk_level: str
    burn_rate: float
    safe_usage_percent: float
    projected_exhaustion_at: datetime | None
    seconds_until_exhaustion: float | None


def compute_depletion_for_account(
    account_id: str,
    limit_name: str,
    window: str,
    history: list,  # list of objects with: used_percent, recorded_at, reset_at, window_minutes
    now: datetime | None = None,
) -> DepletionMetrics | None:
    """
    Compute depletion metrics for a single account using EWMA.

    - history: list of usage entries ordered by recorded_at ASC
    - Returns None if insufficient data (<2 data points) or rate is unknown
    - Uses module-level _ewma_states for in-memory state
    """
    if not history:
        return None

    now = now or utcnow()
    key = (account_id, limit_name, window)
    state = _ewma_states.get(key)

    if len(history) < 2 and state is None:
        entry = history[0]
        _ewma_states[key] = ewma_update(None, entry.used_percent, naive_utc_to_epoch(entry.recorded_at))
        return None

    # Filter out entries already processed by persistent EWMA state to prevent
    # replay drift on repeated dashboard polls.
    if state is not None:
        cutoff = state.last_timestamp
        new_entries = [e for e in history if naive_utc_to_epoch(e.recorded_at) > cutoff]
    else:
        new_entries = history

    for entry in new_entries:
        ts = naive_utc_to_epoch(entry.recorded_at)
        state = ewma_update(state, entry.used_percent, ts)

    if state is not None:
        _ewma_states[key] = state

    if state is None or state.rate is None:
        return None

    latest = history[-1]
    used_percent = latest.used_percent

    # Compute seconds until reset
    seconds_until_reset = 0.0
    if latest.reset_at is not None:
        seconds_until_reset = max(0.0, latest.reset_at - naive_utc_to_epoch(now))
    elif latest.window_minutes is not None:
        window_seconds = latest.window_minutes * 60
        first = history[0]
        elapsed = (now - first.recorded_at).total_seconds()
        seconds_until_reset = max(0.0, window_seconds - elapsed)

    total_window_seconds = (latest.window_minutes * 60) if latest.window_minutes else 0.0
    seconds_elapsed = max(0.0, total_window_seconds - seconds_until_reset)

    risk = compute_depletion_risk(used_percent, state.rate, seconds_until_reset)
    risk_level = classify_risk(risk)
    burn_rate = compute_burn_rate(state.rate, 100.0 - used_percent, seconds_until_reset)
    safe_pct = compute_safe_usage_percent(seconds_elapsed, total_window_seconds)

    projected_exhaustion_at = None
    seconds_until_exhaustion = None
    if state.rate > 0:
        remaining = 100.0 - used_percent
        secs = remaining / state.rate
        seconds_until_exhaustion = secs
        projected_exhaustion_at = now + timedelta(seconds=secs)

    return DepletionMetrics(
        risk=risk,
        risk_level=risk_level,
        rate_per_second=state.rate,
        burn_rate=burn_rate,
        safe_usage_percent=safe_pct,
        projected_exhaustion_at=projected_exhaustion_at,
        seconds_until_exhaustion=seconds_until_exhaustion,
    )


def compute_aggregate_depletion(
    per_account_metrics: list[DepletionMetrics | None],
) -> AggregateDepletionMetrics | None:
    """
    Aggregate depletion metrics across accounts using max(risk).
    Returns None if no valid metrics.
    """
    valid = [m for m in per_account_metrics if m is not None]
    if not valid:
        return None

    risks = [m.risk for m in valid]
    max_risk = aggregate_risks(risks)

    # Average safe_usage_percent and burn_rate across all accounts so the
    # pooled donut marker represents the aggregate, not a single account.
    avg_safe = sum(m.safe_usage_percent for m in valid) / len(valid)
    avg_burn = sum(m.burn_rate for m in valid) / len(valid)

    # Use the earliest projected exhaustion for the pool.
    exhaustion_candidates = [m for m in valid if m.projected_exhaustion_at is not None]
    earliest_exhaustion = (
        min(exhaustion_candidates, key=lambda m: m.projected_exhaustion_at) if exhaustion_candidates else None
    )  # type: ignore[arg-type]

    return AggregateDepletionMetrics(
        risk=max_risk,
        risk_level=classify_risk(max_risk),
        burn_rate=avg_burn,
        safe_usage_percent=avg_safe,
        projected_exhaustion_at=earliest_exhaustion.projected_exhaustion_at if earliest_exhaustion else None,
        seconds_until_exhaustion=earliest_exhaustion.seconds_until_exhaustion if earliest_exhaustion else None,
    )


def reset_ewma_state() -> None:
    """Clear all in-memory EWMA state. Used for testing."""
    _ewma_states.clear()
