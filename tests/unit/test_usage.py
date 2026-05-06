from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.plan_types import account_plan_matches_allowed
from app.core.usage import (
    capacity_for_plan,
    normalize_usage_window,
    normalize_weekly_only_rows,
    summarize_usage_window,
    used_credits_from_percent,
)
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
    assert capacity_for_plan("prolite", "5h") == capacity_for_plan("pro", "5h")
    assert capacity_for_plan("prolite", "7d") == capacity_for_plan("pro", "7d")
    assert capacity_for_plan("unknown", "5h") is None


def test_prolite_matches_pro_model_plan_entitlement():
    assert account_plan_matches_allowed("prolite", frozenset({"pro"})) is True
    assert account_plan_matches_allowed("prolite", frozenset({"plus"})) is False


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

    assert summary.capacity_credits == pytest.approx(capacity_for_plan("pro", "primary") or 0.0)
    assert summary.used_credits == pytest.approx(375.0)
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
