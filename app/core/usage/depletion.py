from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RISK_WARNING = 0.60
RISK_DANGER = 0.80
RISK_CRITICAL = 0.95

DEFAULT_ALPHA = 0.4
RESET_DROP_THRESHOLD = 50.0


@dataclass(frozen=True, slots=True)
class EWMAState:
    rate: float | None
    last_used_percent: float
    last_timestamp: float


def ewma_update(
    state: EWMAState | None,
    used_percent: float,
    timestamp: float,
    alpha: float = DEFAULT_ALPHA,
) -> EWMAState:
    if state is None:
        return EWMAState(
            rate=None,
            last_used_percent=used_percent,
            last_timestamp=timestamp,
        )

    delta_seconds = timestamp - state.last_timestamp
    if delta_seconds <= 0:
        return state

    delta_percent = used_percent - state.last_used_percent
    if delta_percent < -RESET_DROP_THRESHOLD:
        return EWMAState(
            rate=None,
            last_used_percent=used_percent,
            last_timestamp=timestamp,
        )

    instant_rate = max(0.0, delta_percent / delta_seconds)
    smoothed_rate = instant_rate if state.rate is None else alpha * instant_rate + (1 - alpha) * state.rate

    return EWMAState(
        rate=smoothed_rate,
        last_used_percent=used_percent,
        last_timestamp=timestamp,
    )


def compute_burn_rate(
    current_rate: float,
    remaining_percent: float,
    seconds_until_reset: float,
) -> float:
    effective_rate = max(0.0, current_rate)
    if effective_rate == 0.0 or remaining_percent <= 0 or seconds_until_reset <= 0:
        return 0.0

    sustainable_rate = remaining_percent / seconds_until_reset
    return 0.0 if sustainable_rate <= 0 else effective_rate / sustainable_rate


def compute_depletion_risk(
    used_percent: float,
    rate_per_second: float,
    seconds_until_reset: float,
) -> float:
    projected_used = max(0.0, used_percent) + max(0.0, rate_per_second) * max(0.0, seconds_until_reset)
    return min(projected_used / 100.0, 1.0)


def compute_safe_usage_percent(
    seconds_elapsed: float,
    total_window_seconds: float,
) -> float:
    if total_window_seconds <= 0:
        return 100.0

    elapsed_ratio = min(max(seconds_elapsed / total_window_seconds, 0.0), 1.0)
    return elapsed_ratio * 100.0


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
