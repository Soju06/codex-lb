from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.usage import (
    SIBLING_FETCH_MARGIN_SECONDS,
    _should_prefer_primary_row,
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
    # milliseconds newer.
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
        # Written milliseconds after the primary (the race that used to flip).
        recorded_at=now + timedelta(milliseconds=8),
    )

    # Sanity: the two rows are within the same-fetch margin.
    primary_recorded = primary_with_later_reset.recorded_at
    secondary_recorded = secondary_with_earlier_reset.recorded_at
    assert primary_recorded is not None and secondary_recorded is not None
    recorded_delta = abs((primary_recorded - secondary_recorded).total_seconds())
    assert recorded_delta < SIBLING_FETCH_MARGIN_SECONDS

    # Later reset_at wins (primary here), independent of the sub-second ordering.
    assert should_use_weekly_primary(primary_with_later_reset, secondary_with_earlier_reset) is True

    # Swapping the write order must not flip the winner.
    assert (
        should_use_weekly_primary(
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
        )
        is True
    )


def test_cross_fetch_newer_placeholder_beats_stale_real_primary():
    # The regression the gpt-5.6-sol review caught: an older real weekly
    # primary must NOT permanently beat a newer no-data placeholder from a
    # genuinely later fetch (beyond the sibling margin). A later fetch is more
    # authoritative about what upstream currently reports, so the newer row
    # wins even though the older one carries real metadata. (Representing the
    # newer placeholder as "unavailable" rather than 0% is a separate follow-up;
    # the key here is the stale real row no longer freezes the weekly value.)
    now = utcnow()
    stale_real_primary = _real_weekly_primary(
        now - timedelta(days=1),
        used_percent=90.0,
        reset_at=int((now + timedelta(days=6)).timestamp()),
    )
    fresh_placeholder = _no_data_secondary_placeholder(now)

    assert should_use_weekly_primary(stale_real_primary, fresh_placeholder) is False


def test_cross_fetch_newer_real_secondary_beats_stale_real_primary():
    # A genuinely newer real secondary (beyond the margin) still supersedes a
    # stale real weekly primary — restated here to pin the cross-fetch boundary
    # explicitly alongside the placeholder case above.
    now = utcnow()
    stale_real_primary = _real_weekly_primary(now - timedelta(seconds=10), used_percent=65.0, reset_at=100)
    newer_real_secondary = UsageWindowRow(
        account_id="acc_weekly",
        used_percent=15.0,
        window_minutes=10080,
        reset_at=300,
        recorded_at=now,
    )

    assert should_use_weekly_primary(stale_real_primary, newer_real_secondary) is False


def test_same_fetch_boundary_at_exactly_sibling_margin_is_same_fetch():
    # Rows exactly SIBLING_FETCH_MARGIN_SECONDS apart are treated as same-fetch
    # (the impl uses delta > margin for "different fetch", so <= margin is
    # same-fetch). At the boundary the data-aware tiebreak applies, so the real
    # weekly primary beats the placeholder instead of the timestamp deciding.
    now = utcnow()
    real_weekly = _real_weekly_primary(now)
    placeholder_at_boundary = _no_data_secondary_placeholder(now + timedelta(seconds=SIBLING_FETCH_MARGIN_SECONDS))

    assert should_use_weekly_primary(real_weekly, placeholder_at_boundary) is True


def test_both_non_real_same_fetch_rows_fall_back_to_stable_primary_default():
    # Neither row carries real metadata (primary is a weekly window whose
    # reset elapsed: window_minutes=10080, reset_at=None; secondary is a
    # no-data placeholder), and they are in the same fetch. The data-aware
    # tiebreak does not fire, and reset-at precedence finds no deadline on
    # either side, so the stable weekly-primary default wins (returns True).
    now = utcnow()
    elapsed_weekly_primary = UsageWindowRow(
        account_id="acc_weekly",
        used_percent=0.0,
        window_minutes=10080,
        reset_at=None,
        recorded_at=now,
    )
    no_data_secondary = _no_data_secondary_placeholder(now + timedelta(milliseconds=3))

    assert should_use_weekly_primary(elapsed_weekly_primary, no_data_secondary) is True


def test_5h_primary_window_never_enters_weekly_tiebreak():
    # A 5h primary (window_minutes=300) is not a weekly window, so
    # should_use_weekly_primary must return False immediately — the weekly
    # remap tiebreak (and thus this fix's data-aware precedence) never applies
    # to the 5h path.
    now = utcnow()
    five_hour_primary = UsageWindowRow(
        account_id="acc_5h",
        used_percent=40.0,
        window_minutes=300,
        reset_at=int((now + timedelta(hours=3)).timestamp()),
        recorded_at=now,
    )
    no_data_secondary = _no_data_secondary_placeholder(now + timedelta(milliseconds=5))

    assert should_use_weekly_primary(five_hour_primary, no_data_secondary) is False


def test_untimestamped_real_primary_beats_timestamped_placeholder():
    # Exactly-one-missing-timestamp edge case: when only one recorded_at is
    # present, fetch ordering cannot be determined, so timestamp presence must
    # NOT decide the winner. A real weekly primary with recorded_at=None must
    # beat a timestamped no-data secondary placeholder (otherwise this
    # reproduces the original placeholder-wins bug for untimestamped rows).
    now = utcnow()
    untimestamped_real_primary = UsageWindowRow(
        account_id="acc_weekly",
        used_percent=74.0,
        window_minutes=10080,
        reset_at=int((now + timedelta(days=2)).timestamp()),
        recorded_at=None,
    )
    timestamped_placeholder = _no_data_secondary_placeholder(now)

    assert should_use_weekly_primary(untimestamped_real_primary, timestamped_placeholder) is True


def test_timestamped_real_primary_beats_untimestamped_placeholder():
    # Mirror of the above: a real weekly primary with a timestamp must beat an
    # untimestamped no-data secondary placeholder, so a placeholder cannot win
    # merely by being the only timestamped row.
    now = utcnow()
    timestamped_real_primary = _real_weekly_primary(now)
    untimestamped_placeholder = UsageWindowRow(
        account_id="acc_weekly",
        used_percent=0.0,
        window_minutes=0,
        reset_at=None,
        recorded_at=None,
    )

    assert should_use_weekly_primary(timestamped_real_primary, untimestamped_placeholder) is True


def test_weekly_primary_without_reset_at_falls_back_to_stable_default():
    # Partial-metadata matrix: a weekly primary that has positive window_minutes
    # but NO reset_at (e.g. an elapsed window before expiry-rewrite) is NOT
    # "real quota metadata" (needs both). Against a no-data placeholder in the
    # same fetch, neither data-aware branch fires; reset-at precedence finds no
    # deadline on either side; the stable weekly-primary default wins.
    now = utcnow()
    weekly_primary_no_reset = UsageWindowRow(
        account_id="acc_weekly",
        used_percent=50.0,
        window_minutes=10080,
        reset_at=None,
        recorded_at=now,
    )
    placeholder = _no_data_secondary_placeholder(now + timedelta(milliseconds=3))

    assert should_use_weekly_primary(weekly_primary_no_reset, placeholder) is True


def test_helper_timestamped_primary_placeholder_loses_to_untimestamped_real_secondary():
    # The reverse exactly-one-missing case cannot be reached through the public
    # should_use_weekly_primary (a no-data primary has window_minutes=0, so the
    # weekly guard returns False immediately). Test the private tiebreak helper
    # directly to lock down that branch: a timestamped no-data primary must NOT
    # beat an untimestamped real secondary — timestamp presence alone never
    # decides the winner.
    now = utcnow()
    timestamped_primary_placeholder = UsageWindowRow(
        account_id="acc_weekly",
        used_percent=0.0,
        window_minutes=0,
        reset_at=None,
        recorded_at=now,
    )
    untimestamped_real_secondary = UsageWindowRow(
        account_id="acc_weekly",
        used_percent=15.0,
        window_minutes=10080,
        reset_at=300,
        recorded_at=None,
    )

    assert _should_prefer_primary_row(timestamped_primary_placeholder, untimestamped_real_secondary) is False


def test_both_timestamps_none_real_primary_beats_placeholder():
    # Both recorded_at unavailable: fetch ordering cannot be determined, so the
    # data-aware tiebreak runs — a real weekly primary beats a no-data
    # placeholder even with no timestamps on either side.
    now = utcnow()
    real_primary = UsageWindowRow(
        account_id="acc_weekly",
        used_percent=74.0,
        window_minutes=10080,
        reset_at=int((now + timedelta(days=2)).timestamp()),
        recorded_at=None,
    )
    placeholder = UsageWindowRow(
        account_id="acc_weekly",
        used_percent=0.0,
        window_minutes=0,
        reset_at=None,
        recorded_at=None,
    )

    assert _should_prefer_primary_row(real_primary, placeholder) is True
    assert _should_prefer_primary_row(placeholder, real_primary) is False


def test_genuine_both_placeholder_same_fetch_uses_stable_default():
    # Two genuine no-data placeholders (both window_minutes=0, reset_at=None)
    # in the same fetch. Note: this state is unreachable through the public
    # should_use_weekly_primary (the primary would fail the weekly-window
    # guard), so this exercises the private helper to confirm the stable
    # weekly-primary default (True) when no discriminator is available.
    now = utcnow()
    primary_placeholder = UsageWindowRow(
        account_id="acc_weekly",
        used_percent=0.0,
        window_minutes=0,
        reset_at=None,
        recorded_at=now,
    )
    secondary_placeholder = UsageWindowRow(
        account_id="acc_weekly",
        used_percent=0.0,
        window_minutes=0,
        reset_at=None,
        recorded_at=now + timedelta(milliseconds=3),
    )

    assert _should_prefer_primary_row(primary_placeholder, secondary_placeholder) is True


def test_same_fetch_real_rows_secondary_with_later_reset_at_wins():
    # Reverse reset-precedence direction: two real same-fetch weekly rows where
    # the SECONDARY has the later reset_at — it must win (the helper returns
    # False, meaning primary is not preferred).
    now = utcnow()
    primary_earlier_reset = UsageWindowRow(
        account_id="acc_weekly",
        used_percent=50.0,
        window_minutes=10080,
        reset_at=300,
        recorded_at=now,
    )
    secondary_later_reset = UsageWindowRow(
        account_id="acc_weekly",
        used_percent=50.0,
        window_minutes=10080,
        reset_at=400,
        recorded_at=now + timedelta(milliseconds=8),
    )

    assert _should_prefer_primary_row(primary_earlier_reset, secondary_later_reset) is False
