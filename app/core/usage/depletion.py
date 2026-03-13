from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RISK_WARNING = 0.60
RISK_DANGER = 0.80
RISK_CRITICAL = 0.95
DEFAULT_ALPHA = 0.4


@dataclass
class EWMAState:
    rate: float | None
    last_used_percent: float
    last_timestamp: float
    last_reset_at: int | None = None


def ewma_update(
    state: EWMAState | None,
    used_percent: float,
    timestamp: float,
    alpha: float = DEFAULT_ALPHA,
    reset_at: int | None = None,
) -> EWMAState:
    if state is None:
        return EWMAState(
            rate=None,
            last_used_percent=used_percent,
            last_timestamp=timestamp,
            last_reset_at=reset_at,
        )

    dt = timestamp - state.last_timestamp
    if dt == 0:
        return state

    window_changed = reset_at is not None and state.last_reset_at is not None and reset_at != state.last_reset_at

    drop = state.last_used_percent - used_percent
    if drop > 0 or window_changed:
        return EWMAState(
            rate=None,
            last_used_percent=used_percent,
            last_timestamp=timestamp,
            last_reset_at=reset_at,
        )

    delta_percent = used_percent - state.last_used_percent
    raw_rate = max(delta_percent / dt, 0.0)
    rate = raw_rate if state.rate is None else (alpha * raw_rate) + ((1 - alpha) * state.rate)

    return EWMAState(
        rate=rate,
        last_used_percent=used_percent,
        last_timestamp=timestamp,
        last_reset_at=reset_at,
    )


def compute_burn_rate(
    current_rate: float,
    remaining_percent: float,
    seconds_until_reset: float,
) -> float:
    if current_rate == 0 or seconds_until_reset == 0:
        return 0.0

    sustainable_rate = remaining_percent / seconds_until_reset
    if sustainable_rate == 0:
        return 0.0
    return current_rate / sustainable_rate


def compute_depletion_risk(
    used_percent: float,
    rate_per_second: float,
    seconds_until_reset: float,
) -> float:
    effective_rate = max(0.0, rate_per_second)
    projected = used_percent + (effective_rate * seconds_until_reset)
    return min(projected / 100.0, 1.0)


def compute_safe_usage_percent(
    seconds_elapsed: float,
    total_window_seconds: float,
) -> float:
    if total_window_seconds == 0:
        return 0.0

    progress = seconds_elapsed / total_window_seconds
    clamped_progress = min(max(progress, 0.0), 1.0)
    return clamped_progress * 100.0


def classify_risk(risk: float) -> Literal["safe", "warning", "danger", "critical"]:
    if risk >= RISK_CRITICAL:
        return "critical"
    if risk >= RISK_DANGER:
        return "danger"
    if risk >= RISK_WARNING:
        return "warning"
    return "safe"


def aggregate_risks(risks: list[float]) -> float:
    return max(risks) if risks else 0.0
