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
    account_id: str = "acc_1",
    recorded_at: datetime = NOW,
    reset_delta: timedelta | None = timedelta(hours=1),
    window_minutes: int | None = 300,
) -> UsageWindowRow:
    return UsageWindowRow(
        account_id=account_id,
        used_percent=used_percent,
        reset_at=int((NOW + reset_delta).timestamp()) if reset_delta is not None else None,
        window_minutes=window_minutes,
        recorded_at=recorded_at,
    )


def _evaluate(
    *,
    enabled: bool = True,
    limit_percent: float | None = 10.0,
    plan_type: str = "plus",
    primary: UsageWindowRow | None = None,
    secondary: UsageWindowRow | None = None,
    monthly: UsageWindowRow | None = None,
) -> AccountUsageLimitState:
    return evaluate_standard_usage_limit(
        enabled=enabled,
        limit_percent=limit_percent,
        plan_type=plan_type,
        primary=primary,
        secondary=secondary,
        monthly=monthly,
        now=NOW,
        refresh_interval_seconds=60,
    )


def test_disabled_limit_does_not_require_usage_data() -> None:
    assert _evaluate(enabled=False, limit_percent=10.0) is AccountUsageLimitState.DISABLED


def test_current_usage_below_limit_is_available() -> None:
    assert _evaluate(primary=_row(9.99)) is AccountUsageLimitState.AVAILABLE


def test_fresh_zero_percent_usage_with_quota_metadata_is_available() -> None:
    assert _evaluate(primary=_row(0.0)) is AccountUsageLimitState.AVAILABLE


@pytest.mark.parametrize("window_minutes", [0, None], ids=["zero-window", "missing-window"])
def test_fresh_no_data_placeholder_is_unavailable(window_minutes: int | None) -> None:
    placeholder = _row(0.0, reset_delta=None, window_minutes=window_minutes)

    assert _evaluate(primary=placeholder) is AccountUsageLimitState.DATA_UNAVAILABLE


@pytest.mark.parametrize("used_percent", [10.0, 10.01, 100.0])
def test_usage_at_or_above_limit_is_reached(used_percent: float) -> None:
    assert _evaluate(primary=_row(used_percent)) is AccountUsageLimitState.REACHED


@pytest.mark.parametrize(
    ("primary", "limit_percent"),
    [
        (None, 10.0),
        (_row(None), 10.0),
        (_row(5.0, recorded_at=NOW - timedelta(seconds=181)), 10.0),
        (_row(5.0), None),
    ],
)
def test_enabled_limit_fails_closed_without_usable_current_data(
    primary: UsageWindowRow | None,
    limit_percent: float | None,
) -> None:
    assert _evaluate(primary=primary, limit_percent=limit_percent) is AccountUsageLimitState.DATA_UNAVAILABLE


def test_elapsed_window_is_ignored_when_another_current_window_is_available() -> None:
    assert (
        _evaluate(
            primary=_row(100.0, reset_delta=timedelta(seconds=-1)),
            secondary=_row(5.0, window_minutes=10080),
        )
        is AccountUsageLimitState.AVAILABLE
    )


def test_elapsed_only_window_requires_a_post_reset_observation() -> None:
    assert _evaluate(primary=_row(100.0, reset_delta=timedelta(seconds=-1))) is AccountUsageLimitState.DATA_UNAVAILABLE


def test_weekly_only_primary_is_evaluated_as_the_long_window() -> None:
    assert _evaluate(primary=_row(10.0, window_minutes=10080), secondary=None) is AccountUsageLimitState.REACHED


def test_newer_weekly_primary_replaces_an_older_secondary_row() -> None:
    weekly_primary = _row(5.0, recorded_at=NOW, window_minutes=10080)
    older_secondary = _row(
        99.0,
        recorded_at=NOW - timedelta(minutes=10),
        window_minutes=10080,
    )

    assert _evaluate(primary=weekly_primary, secondary=older_secondary) is AccountUsageLimitState.AVAILABLE


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
            secondary=_row(
                5.0,
                recorded_at=NOW - timedelta(seconds=181),
                window_minutes=10080,
            ),
        )
        is AccountUsageLimitState.DATA_UNAVAILABLE
    )


def test_fresh_weekly_primary_supersedes_old_monthly_shape() -> None:
    assert (
        _evaluate(
            plan_type="free",
            primary=_row(5.0, window_minutes=10080),
            monthly=_row(
                100.0,
                recorded_at=NOW - timedelta(minutes=10),
                reset_delta=timedelta(seconds=-1),
                window_minutes=43200,
            ),
        )
        is AccountUsageLimitState.AVAILABLE
    )


def test_newer_monthly_shape_supersedes_weekly_primary() -> None:
    assert (
        _evaluate(
            plan_type="free",
            primary=_row(
                5.0,
                recorded_at=NOW - timedelta(seconds=10),
                window_minutes=10080,
            ),
            monthly=_row(10.0, window_minutes=43200),
        )
        is AccountUsageLimitState.REACHED
    )


def test_newest_normalized_shape_still_fails_closed_when_stale() -> None:
    assert (
        _evaluate(
            plan_type="free",
            primary=_row(
                5.0,
                recorded_at=NOW - timedelta(seconds=181),
                window_minutes=10080,
            ),
            monthly=_row(
                5.0,
                recorded_at=NOW - timedelta(minutes=10),
                window_minutes=43200,
            ),
        )
        is AccountUsageLimitState.DATA_UNAVAILABLE
    )


def test_monthly_and_weekly_shapes_at_sibling_boundary_use_reset_tiebreak() -> None:
    assert (
        _evaluate(
            plan_type="free",
            primary=_row(
                5.0,
                recorded_at=NOW,
                reset_delta=timedelta(hours=1),
                window_minutes=10080,
            ),
            monthly=_row(
                10.0,
                recorded_at=NOW - timedelta(seconds=SIBLING_FETCH_MARGIN_SECONDS),
                reset_delta=timedelta(hours=2),
                window_minutes=43200,
            ),
        )
        is AccountUsageLimitState.REACHED
    )


def test_monthly_and_weekly_exact_tie_uses_stable_weekly_primary_default() -> None:
    assert (
        _evaluate(
            plan_type="free",
            primary=_row(5.0, window_minutes=10080),
            monthly=_row(10.0, window_minutes=43200),
        )
        is AccountUsageLimitState.AVAILABLE
    )
