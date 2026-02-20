from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.usage import (
    capacity_for_plan,
    normalize_usage_window,
    normalize_weekly_only_rows,
    used_credits_from_percent,
)
from app.core.usage.types import UsageWindowRow, UsageWindowSummary
from app.core.utils.time import utcnow

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
    assert capacity_for_plan("unknown", "5h") is None


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
