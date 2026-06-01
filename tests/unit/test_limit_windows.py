from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.db.models import LimitWindow
from app.modules.api_keys.limit_windows import (
    advance_limit_reset,
    limit_window_delta,
    next_limit_reset,
)

pytestmark = pytest.mark.unit


NOW = datetime(2026, 5, 28, 10, 30, 0)


@pytest.mark.parametrize(
    "window, expected_delta",
    [
        (LimitWindow.FIVE_HOURS, timedelta(hours=5)),
        (LimitWindow.DAILY, timedelta(days=1)),
        (LimitWindow.WEEKLY, timedelta(days=7)),
        (LimitWindow.SEVEN_DAYS, timedelta(days=7)),
        (LimitWindow.MONTHLY, timedelta(days=30)),
    ],
)
def test_limit_window_delta_returns_expected_duration(
    window: LimitWindow,
    expected_delta: timedelta,
) -> None:
    assert limit_window_delta(window) == expected_delta


@pytest.mark.parametrize(
    "window, expected",
    [
        (LimitWindow.FIVE_HOURS, NOW + timedelta(hours=5)),
        (LimitWindow.DAILY, NOW + timedelta(days=1)),
        (LimitWindow.WEEKLY, NOW + timedelta(days=7)),
        (LimitWindow.SEVEN_DAYS, NOW + timedelta(days=7)),
        (LimitWindow.MONTHLY, NOW + timedelta(days=30)),
    ],
)
def test_next_limit_reset_adds_window_delta_to_now(
    window: LimitWindow,
    expected: datetime,
) -> None:
    assert next_limit_reset(NOW, window) == expected


def test_advance_limit_reset_returns_input_when_reset_is_already_in_future() -> None:
    reset_at = NOW + timedelta(hours=1)

    assert advance_limit_reset(reset_at, NOW, LimitWindow.DAILY) == reset_at


def test_advance_limit_reset_returns_input_when_reset_equals_now_strictly() -> None:
    # Boundary check: `next_reset <= now` enters the loop, so equality must
    # still advance the reset stamp. Without this guard a limit could remain
    # pinned to the same wall-clock instant indefinitely if the scheduler
    # fires exactly on the boundary.
    reset_at = NOW

    result = advance_limit_reset(reset_at, NOW, LimitWindow.DAILY)

    assert result == NOW + timedelta(days=1)


def test_advance_limit_reset_advances_by_a_single_delta_when_one_window_passed() -> None:
    reset_at = NOW - timedelta(hours=1)

    result = advance_limit_reset(reset_at, NOW, LimitWindow.DAILY)

    assert result == reset_at + timedelta(days=1)


def test_advance_limit_reset_advances_multiple_deltas_when_many_windows_passed() -> None:
    # Three full daily windows missed should bump reset_at by exactly three
    # deltas, not just one. Pins the loop semantic so future refactors that
    # use a different `>=` boundary still walk the right number of windows.
    reset_at = NOW - timedelta(days=3, hours=2)

    result = advance_limit_reset(reset_at, NOW, LimitWindow.DAILY)

    assert result == reset_at + timedelta(days=4)
    assert result > NOW


def test_advance_limit_reset_handles_long_idle_gap_for_monthly_window() -> None:
    # 18 months of missed monthly resets should not loop forever; pins the
    # behaviour so a future per-call cap (or vectorised math) doesn't drop
    # this case below the >now boundary.
    reset_at = NOW - timedelta(days=30 * 18)

    result = advance_limit_reset(reset_at, NOW, LimitWindow.MONTHLY)

    assert result > NOW
    assert result <= NOW + timedelta(days=30)


@pytest.mark.parametrize(
    "window",
    [
        LimitWindow.FIVE_HOURS,
        LimitWindow.DAILY,
        LimitWindow.WEEKLY,
        LimitWindow.SEVEN_DAYS,
        LimitWindow.MONTHLY,
    ],
)
def test_advance_limit_reset_lands_in_future_for_every_window(window: LimitWindow) -> None:
    # Across every supported window enum, advancing from a stale stamp must
    # always land strictly in the future relative to `now`.
    reset_at = NOW - timedelta(days=365)

    result = advance_limit_reset(reset_at, NOW, window)

    assert result > NOW
