from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.usage import SIBLING_FETCH_MARGIN_SECONDS
from app.core.usage.account_limits import AccountUsageLimitState, evaluate_standard_usage_limit
from app.core.usage.types import UsageWindowRow

NOW = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)


def _row(
    used_percent: float | None,
    *,
    recorded_at: datetime = NOW,
    reset_delta: timedelta | None = timedelta(hours=1),
    window_minutes: int | None = 300,
) -> UsageWindowRow:
    return UsageWindowRow(
        account_id="acc_1",
        used_percent=used_percent,
        reset_at=int((NOW + reset_delta).timestamp()) if reset_delta is not None else None,
        window_minutes=window_minutes,
        recorded_at=recorded_at,
    )


def _evaluate(
    *,
    enabled: bool = True,
    limit_percent: float | None = 10.0,
    limit_5h_percent: float | None = None,
    limit_weekly_percent: float | None = None,
    plan_type: str = "plus",
    primary: UsageWindowRow | None = None,
    secondary: UsageWindowRow | None = None,
    monthly: UsageWindowRow | None = None,
    refresh_interval_seconds: int = 60,
) -> AccountUsageLimitState:
    return evaluate_standard_usage_limit(
        enabled=enabled,
        limit_percent=limit_percent,
        limit_5h_percent=limit_5h_percent,
        limit_weekly_percent=limit_weekly_percent,
        plan_type=plan_type,
        primary=primary,
        secondary=secondary,
        monthly=monthly,
        now=NOW,
        refresh_interval_seconds=refresh_interval_seconds,
    )


def test_disabled_limit_does_not_require_usage_data() -> None:
    assert _evaluate(enabled=False) is AccountUsageLimitState.DISABLED


@pytest.mark.parametrize("used_percent", [0.0, 9.99])
def test_fresh_usage_below_limit_is_available(used_percent: float) -> None:
    assert _evaluate(primary=_row(used_percent)) is AccountUsageLimitState.AVAILABLE


@pytest.mark.parametrize("window_minutes", [0, None], ids=["zero-window", "missing-window"])
def test_fresh_no_data_placeholder_is_unavailable(window_minutes: int | None) -> None:
    assert (
        _evaluate(primary=_row(0.0, reset_delta=None, window_minutes=window_minutes))
        is AccountUsageLimitState.DATA_UNAVAILABLE
    )


def test_usage_at_limit_is_reached() -> None:
    assert _evaluate(primary=_row(10.0)) is AccountUsageLimitState.REACHED


@pytest.mark.parametrize(
    ("primary", "limit_percent"),
    [
        (None, 10.0),
        (_row(None), 10.0),
        (_row(5.0), None),
    ],
    ids=["missing-row", "missing-measurement", "missing-limit"],
)
def test_enabled_limit_fails_closed_without_complete_inputs(
    primary: UsageWindowRow | None,
    limit_percent: float | None,
) -> None:
    assert _evaluate(primary=primary, limit_percent=limit_percent) is AccountUsageLimitState.DATA_UNAVAILABLE


@pytest.mark.parametrize(
    ("refresh_interval_seconds", "age_seconds", "expected"),
    [
        (120, 240, AccountUsageLimitState.REACHED),
        (120, 241, AccountUsageLimitState.DATA_UNAVAILABLE),
        (60, 180, AccountUsageLimitState.REACHED),
        (60, 181, AccountUsageLimitState.DATA_UNAVAILABLE),
    ],
    ids=[
        "double-interval-boundary-is-fresh",
        "past-double-interval-is-stale",
        "minimum-floor-boundary-is-fresh",
        "past-minimum-floor-is-stale",
    ],
)
def test_freshness_cutoff_is_max_of_double_refresh_interval_and_floor(
    refresh_interval_seconds: int,
    age_seconds: int,
    expected: AccountUsageLimitState,
) -> None:
    row = _row(100.0, recorded_at=NOW - timedelta(seconds=age_seconds))

    assert _evaluate(primary=row, refresh_interval_seconds=refresh_interval_seconds) is expected


def test_elapsed_window_is_ignored_when_another_current_window_is_available() -> None:
    assert (
        _evaluate(
            primary=_row(100.0, reset_delta=timedelta(seconds=-1)),
            secondary=_row(5.0, window_minutes=10080),
        )
        is AccountUsageLimitState.AVAILABLE
    )


def test_elapsed_only_window_is_unavailable() -> None:
    assert _evaluate(primary=_row(100.0, reset_delta=timedelta(seconds=-1))) is AccountUsageLimitState.DATA_UNAVAILABLE


def test_weekly_only_primary_is_evaluated_as_the_long_window() -> None:
    assert _evaluate(primary=_row(10.0, window_minutes=10080)) is AccountUsageLimitState.REACHED


def test_newer_weekly_primary_replaces_an_older_secondary_row() -> None:
    assert (
        _evaluate(
            primary=_row(5.0, window_minutes=10080),
            secondary=_row(99.0, recorded_at=NOW - timedelta(minutes=10), window_minutes=10080),
        )
        is AccountUsageLimitState.AVAILABLE
    )


def test_monthly_only_plan_uses_monthly_instead_of_standard_slots() -> None:
    assert (
        _evaluate(
            plan_type="free",
            primary=_row(100.0),
            secondary=_row(100.0, window_minutes=10080),
            monthly=_row(5.0, window_minutes=43200),
        )
        is AccountUsageLimitState.AVAILABLE
    )


def test_stale_relevant_window_fails_closed_even_when_another_window_is_fresh() -> None:
    assert (
        _evaluate(
            primary=_row(5.0),
            secondary=_row(5.0, recorded_at=NOW - timedelta(seconds=181), window_minutes=10080),
        )
        is AccountUsageLimitState.DATA_UNAVAILABLE
    )


@pytest.mark.parametrize(
    ("primary", "monthly", "expected"),
    [
        (
            _row(5.0, window_minutes=10080),
            _row(
                100.0,
                recorded_at=NOW - timedelta(minutes=10),
                reset_delta=timedelta(seconds=-1),
                window_minutes=43200,
            ),
            AccountUsageLimitState.AVAILABLE,
        ),
        (
            _row(5.0, recorded_at=NOW - timedelta(seconds=10), window_minutes=10080),
            _row(10.0, window_minutes=43200),
            AccountUsageLimitState.REACHED,
        ),
        (
            _row(5.0, window_minutes=10080),
            _row(10.0, window_minutes=43200),
            AccountUsageLimitState.AVAILABLE,
        ),
        (
            _row(5.0, reset_delta=timedelta(hours=1), window_minutes=10080),
            _row(
                10.0,
                recorded_at=NOW - timedelta(seconds=SIBLING_FETCH_MARGIN_SECONDS),
                reset_delta=timedelta(hours=2),
                window_minutes=43200,
            ),
            AccountUsageLimitState.REACHED,
        ),
        (
            _row(5.0, recorded_at=NOW - timedelta(seconds=181), window_minutes=10080),
            _row(5.0, recorded_at=NOW - timedelta(minutes=10), window_minutes=43200),
            AccountUsageLimitState.DATA_UNAVAILABLE,
        ),
    ],
    ids=[
        "newer-weekly-shape",
        "newer-monthly-shape",
        "exact-tie-keeps-weekly-primary",
        "sibling-boundary-uses-reset-tiebreak",
        "selected-monthly-shape-is-stale",
    ],
)
def test_free_plan_evaluates_one_effective_long_window(
    primary: UsageWindowRow,
    monthly: UsageWindowRow,
    expected: AccountUsageLimitState,
) -> None:
    assert _evaluate(plan_type="free", primary=primary, monthly=monthly) is expected


@pytest.mark.parametrize(
    ("primary_used", "weekly_used", "expected"),
    [
        (65, 75, AccountUsageLimitState.AVAILABLE),
        (70, 75, AccountUsageLimitState.REACHED),
        (65, 90, AccountUsageLimitState.REACHED),
    ],
)
def test_unequal_overrides_replace_default_per_window(primary_used, weekly_used, expected):
    assert (
        _evaluate(
            limit_percent=80,
            limit_5h_percent=70,
            limit_weekly_percent=90,
            primary=_row(primary_used),
            secondary=_row(weekly_used, window_minutes=10080),
        )
        is expected
    )


@pytest.mark.parametrize("window_minutes", [300, 43200, 1440])
def test_standalone_weekly_override_does_not_limit_other_durations(window_minutes):
    assert (
        _evaluate(
            limit_percent=None,
            limit_weekly_percent=20,
            primary=_row(80, window_minutes=window_minutes),
        )
        is AccountUsageLimitState.AVAILABLE
    )


def test_weekly_override_follows_weekly_only_primary_normalization():
    assert (
        _evaluate(
            limit_percent=90,
            limit_weekly_percent=70,
            primary=_row(75, window_minutes=10080),
        )
        is AccountUsageLimitState.REACHED
    )


def test_override_preserves_fail_closed_freshness():
    assert (
        _evaluate(
            limit_percent=None,
            limit_weekly_percent=90,
            secondary=_row(5, window_minutes=10080, recorded_at=NOW - timedelta(minutes=10)),
        )
        is AccountUsageLimitState.DATA_UNAVAILABLE
    )


def test_standalone_override_cannot_treat_unknown_window_placeholder_as_unrestricted():
    assert (
        _evaluate(
            limit_percent=None,
            limit_weekly_percent=90,
            primary=_row(0, window_minutes=None, reset_delta=None),
        )
        is AccountUsageLimitState.DATA_UNAVAILABLE
    )
