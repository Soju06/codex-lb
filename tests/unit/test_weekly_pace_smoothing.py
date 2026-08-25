from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.dashboard.weekly_pace import _recent_burn_rate_credits_per_hour

pytestmark = pytest.mark.unit

BASE_TIME = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


@dataclass
class _UsageRow:
    used_percent: float
    recorded_at: datetime
    reset_at: int | None = 2_000_000_000
    window_minutes: int | None = 10_080


def _row(minutes: int, used_percent: float) -> _UsageRow:
    return _UsageRow(used_percent=used_percent, recorded_at=BASE_TIME + timedelta(minutes=minutes))


def test_quantized_percent_step_is_damped_by_time_based_smoothing() -> None:
    rows = [_row(0, 20.0), _row(1, 20.0), _row(2, 21.0)]

    rate = _recent_burn_rate_credits_per_hour(
        rows,
        full_credits=100.0,
        now=BASE_TIME + timedelta(minutes=2),
        smoothing_window_minutes=30,
    )

    adjacent_sample_spike = 100.0
    assert rate is not None
    assert 0.0 < rate < adjacent_sample_spike / 10.0


def test_equivalent_wall_clock_usage_is_stable_across_refresh_cadence() -> None:
    one_minute_rows = [_row(minute, minute / 60.0) for minute in range(61)]
    five_minute_rows = [_row(minute, minute / 60.0) for minute in range(0, 61, 5)]
    now = BASE_TIME + timedelta(hours=1)

    one_minute_rate = _recent_burn_rate_credits_per_hour(
        one_minute_rows,
        full_credits=100.0,
        now=now,
        smoothing_window_minutes=30,
    )
    five_minute_rate = _recent_burn_rate_credits_per_hour(
        five_minute_rows,
        full_credits=100.0,
        now=now,
        smoothing_window_minutes=30,
    )

    assert one_minute_rate is not None
    assert five_minute_rate is not None
    assert one_minute_rate == pytest.approx(five_minute_rate, rel=0.02)


def test_non_finite_samples_are_ignored() -> None:
    rows = [_row(0, 10.0), _row(1, float("nan")), _row(2, 11.0)]

    rate = _recent_burn_rate_credits_per_hour(
        rows,
        full_credits=100.0,
        now=BASE_TIME + timedelta(minutes=2),
        smoothing_window_minutes=30,
    )

    assert rate is not None
    assert rate > 0.0
