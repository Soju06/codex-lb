from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.usage import (
    SIBLING_FETCH_MARGIN_SECONDS,
    capacity_for_plan,
    normalize_rate_limit_windows,
    normalize_usage_window,
    normalize_weekly_only_rows,
    should_use_weekly_primary,
    summarize_usage_window,
    used_credits_from_percent,
)
from app.core.usage.models import UsageWindow
from app.core.usage.types import UsageWindowRow, UsageWindowSummary
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus

pytestmark = pytest.mark.unit


def test_used_credits_from_percent():
    assert used_credits_from_percent(25.0, 200.0) == 50.0
    assert used_credits_from_percent(None, 200.0) is None


def test_normalize_usage_window_defaults():
    summary = UsageWindowSummary(
        used_percent=None,
        capacity_credits=0.0,
        used_credits=0.0,
        reset_at=None,
        window_minutes=None,
    )
    window = normalize_usage_window(summary)
    assert window.used_percent == 0.0
    assert window.capacity_credits == 0.0
    assert window.used_credits == 0.0


def test_capacity_for_plan():
    assert capacity_for_plan("plus", "5h") is not None
    assert capacity_for_plan("plus", "7d") is not None
    assert capacity_for_plan("prolite", "5h") == pytest.approx(1125.0)
    assert capacity_for_plan("prolite", "7d") == pytest.approx(37800.0)
    assert capacity_for_plan("unknown", "5h") is None


def test_summarize_usage_window_includes_prolite_capacity():
    account = Account(
        id="acc_prolite",
        email="prolite@example.com",
        plan_type="prolite",
        access_token_encrypted=b"access",
        refresh_token_encrypted=b"refresh",
        id_token_encrypted=b"id",
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
    )
    row = UsageWindowRow(
        account_id=account.id,
        used_percent=25.0,
        reset_at=123,
        window_minutes=300,
        recorded_at=utcnow(),
    )

    summary = summarize_usage_window([row], {account.id: account}, "primary")

    assert summary.capacity_credits == pytest.approx(1125.0)
    assert summary.used_credits == pytest.approx(281.25)
    assert summary.used_percent == pytest.approx(25.0)


def test_normalize_weekly_only_rows_prefers_newer_primary_over_stale_secondary():
    now = utcnow()
    weekly_primary = UsageWindowRow(
        account_id="acc_weekly",
        used_percent=65.0,
        window_minutes=10080,
        reset_at=300,
        recorded_at=now,
    )
    stale_secondary = UsageWindowRow(
        account_id="acc_weekly",
        used_percent=5.0,
        window_minutes=10080,
        reset_at=100,
        recorded_at=now - timedelta(days=2),
    )

    normalized_primary, normalized_secondary = normalize_weekly_only_rows(
        [weekly_primary],
        [stale_secondary],
    )

    assert normalized_primary == []
    assert normalized_secondary == [weekly_primary]


def test_normalize_weekly_only_rows_keeps_newer_secondary():
    now = utcnow()
    older_weekly_primary = UsageWindowRow(
        account_id="acc_weekly",
        used_percent=65.0,
        window_minutes=10080,
        reset_at=100,
        recorded_at=now - timedelta(days=1),
    )
    newer_secondary = UsageWindowRow(
        account_id="acc_weekly",
        used_percent=15.0,
        window_minutes=10080,
        reset_at=300,
        recorded_at=now,
    )

    normalized_primary, normalized_secondary = normalize_weekly_only_rows(
        [older_weekly_primary],
        [newer_secondary],
    )

    assert normalized_primary == []
    assert normalized_secondary == [newer_secondary]


def test_normalize_rate_limit_windows_promotes_monthly_primary_without_secondary() -> None:
    primary = UsageWindow(
        used_percent=5.0,
        limit_window_seconds=2_592_000,
        reset_at=1_800_000_000,
    )

    normalized = normalize_rate_limit_windows(primary, None)

    assert normalized.primary is None
    assert normalized.secondary is None
    assert normalized.monthly is primary


def _real_weekly_primary(now, *, used_percent: float = 74.0, reset_at: int = 1_800_000_000) -> UsageWindowRow:
    """A weekly window reported in the primary slot with real quota metadata."""
    return UsageWindowRow(
        account_id="acc_weekly",
        used_percent=used_percent,
        window_minutes=10080,
        reset_at=reset_at,
        recorded_at=now,
    )


def _no_data_secondary_placeholder(recorded_at) -> UsageWindowRow:
    """An empty secondary slot: no window duration, no reset, 0% used.

    This is the shape upstream sends when it omits the secondary window but the
    updater still persists a placeholder row in the same fetch as the primary.
    """
    return UsageWindowRow(
        account_id="acc_weekly",
        used_percent=0.0,
        window_minutes=0,
        reset_at=None,
        recorded_at=recorded_at,
    )


def test_should_use_weekly_primary_beats_no_data_secondary_placeholder_regardless_of_write_order():
    # The live bug: the secondary placeholder is written ~10ms after the real
    # weekly primary in the same fetch, so a recorded_at tiebreak let the
    # placeholder win and the dashboard jumped to 100% remaining. The data-aware
    # tiebreak must let the real weekly primary win regardless of which row is
    # microseconds newer.
    now = utcnow()
    real_weekly = _real_weekly_primary(now)
    placeholder_written_after = _no_data_secondary_placeholder(now + timedelta(milliseconds=13))
    placeholder_written_before = _no_data_secondary_placeholder(now - timedelta(milliseconds=13))

    assert should_use_weekly_primary(real_weekly, placeholder_written_after) is True
    assert should_use_weekly_primary(real_weekly, placeholder_written_before) is True

    # The remap must surface the real weekly usage on the secondary slot.
    normalized_primary, normalized_secondary = normalize_weekly_only_rows(
        [real_weekly],
        [placeholder_written_after],
    )
    assert normalized_primary == []
    assert normalized_secondary == [real_weekly]


def test_should_use_weekly_primary_beats_no_data_placeholder_with_null_window_minutes():
    # The placeholder may also carry NULL window_minutes (upstream omits the
    # window entirely). Both 0 and None must classify as no-data.
    now = utcnow()
    real_weekly = _real_weekly_primary(now)
    placeholder = UsageWindowRow(
        account_id="acc_weekly",
        used_percent=0.0,
        window_minutes=None,
        reset_at=None,
        recorded_at=now + timedelta(milliseconds=5),
    )

    assert should_use_weekly_primary(real_weekly, placeholder) is True


def test_genuinely_newer_real_secondary_supersedes_stale_weekly_primary():
    # When a real secondary row arrives in a genuinely later fetch (beyond the
    # sibling-fetch margin), it must still supersede a stale weekly primary.
    now = utcnow()
    stale_weekly_primary = _real_weekly_primary(
        now - timedelta(days=1),
        used_percent=65.0,
        reset_at=100,
    )
    newer_real_secondary = UsageWindowRow(
        account_id="acc_weekly",
        used_percent=15.0,
        window_minutes=10080,
        reset_at=300,
        recorded_at=now,
    )

    assert should_use_weekly_primary(stale_weekly_primary, newer_real_secondary) is False

    _, normalized_secondary = normalize_weekly_only_rows(
        [stale_weekly_primary],
        [newer_real_secondary],
    )
    assert normalized_secondary == [newer_real_secondary]


def test_two_real_same_fetch_weekly_rows_resolve_by_reset_at_not_subsecond_timing():
    # Two real weekly rows written in the same fetch (within the sibling margin)
    # must be resolved by reset-at precedence, not by a sub-second recorded_at
    # difference, so the winner does not flip across refresh cycles.
    now = utcnow()
    primary_with_later_reset = UsageWindowRow(
        account_id="acc_weekly",
        used_percent=50.0,
        window_minutes=10080,
        reset_at=400,
        recorded_at=now,
    )
    secondary_with_earlier_reset = UsageWindowRow(
        account_id="acc_weekly",
        used_percent=50.0,
        window_minutes=10080,
        reset_at=300,
        # Written microseconds after the primary (the race that used to flip).
        recorded_at=now + timedelta(milliseconds=8),
    )

    # Sanity: the two rows are within the same-fetch margin.
    recorded_delta = abs(
        (primary_with_later_reset.recorded_at - secondary_with_earlier_reset.recorded_at).total_seconds()
    )
    assert recorded_delta < SIBLING_FETCH_MARGIN_SECONDS

    # Later reset_at wins (primary here), independent of the sub-second ordering.
    assert should_use_weekly_primary(primary_with_later_reset, secondary_with_earlier_reset) is True

    # Swapping the write order must not flip the winner.
    assert should_use_weekly_primary(
        UsageWindowRow(
            account_id="acc_weekly",
            used_percent=50.0,
            window_minutes=10080,
            reset_at=400,
            recorded_at=now + timedelta(milliseconds=8),
        ),
        UsageWindowRow(
            account_id="acc_weekly",
            used_percent=50.0,
            window_minutes=10080,
            reset_at=300,
            recorded_at=now,
        ),
    ) is True
