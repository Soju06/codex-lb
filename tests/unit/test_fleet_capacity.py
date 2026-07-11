from __future__ import annotations

from datetime import timedelta

from app.core.utils.time import utcnow
from app.modules.accounts.schemas import (
    AccountAdditionalQuota,
    AccountAdditionalWindow,
    AccountSummary,
    AccountUsage,
)
from app.modules.fleet.mappers import build_fleet_capacity


def _account(
    account_id: str,
    *,
    status: str = "active",
    primary_remaining: float | None = 100.0,
    secondary_remaining: float | None = 100.0,
    refreshed_minutes_ago: int = 0,
    additional_quotas: list[AccountAdditionalQuota] | None = None,
) -> AccountSummary:
    return AccountSummary(
        account_id=account_id,
        email=f"{account_id}@onda.lol",
        display_name=account_id,
        plan_type="plus",
        status=status,
        usage=AccountUsage(
            primary_remaining_percent=primary_remaining,
            secondary_remaining_percent=secondary_remaining,
        ),
        last_refresh_at=utcnow() - timedelta(minutes=refreshed_minutes_ago),
        additional_quotas=additional_quotas or [],
    )


def test_capacity_averages_used_percent_rounds_and_clamps() -> None:
    now = utcnow()
    accounts = [
        _account("a", primary_remaining=100.0, secondary_remaining=0.0),
        _account("b", primary_remaining=28.8, secondary_remaining=-10.0),
    ]
    recorded_at = {account.account_id: now for account in accounts}

    included, excluded, five_hour, weekly, additional = build_fleet_capacity(
        accounts,
        include_usage=True,
        generated_at=now,
        primary_recorded_at_by_account=recorded_at,
        secondary_recorded_at_by_account=recorded_at,
    )

    assert included == ["a", "b"]
    assert excluded == []
    assert five_hour.used_percent == 36
    assert five_hour.stale is False
    assert weekly.used_percent == 100
    assert weekly.stale is False
    assert additional == []


def test_capacity_excludes_unroutable_and_suppresses_stale_or_missing_headline() -> None:
    now = utcnow()
    accounts = [
        _account("fresh", primary_remaining=50.0, secondary_remaining=None),
        _account("stale", primary_remaining=20.0),
        _account("paused", status="paused", primary_remaining=0.0),
        _account("deactivated", status="deactivated", primary_remaining=0.0),
    ]
    primary_recorded_at = {"fresh": now, "stale": now - timedelta(minutes=11)}
    secondary_recorded_at = {"fresh": now, "stale": now}

    included, excluded, five_hour, weekly, _additional = build_fleet_capacity(
        accounts,
        include_usage=True,
        generated_at=now,
        primary_recorded_at_by_account=primary_recorded_at,
        secondary_recorded_at_by_account=secondary_recorded_at,
    )

    assert included == ["fresh", "stale"]
    assert [(item.account_id, item.status) for item in excluded] == [
        ("paused", "paused"),
        ("deactivated", "deactivated"),
    ]
    assert five_hour.used_percent is None
    assert five_hour.stale_reason == "stale_usage:stale"
    assert weekly.used_percent is None
    assert weekly.stale_reason == "missing_usage:fresh"


def test_capacity_reports_sanitized_additional_quota_and_hides_it_without_visibility() -> None:
    now = utcnow()
    quota = AccountAdditionalQuota(
        quota_key="spark",
        limit_name="Spark",
        metered_feature="spark",
        display_label="Spark",
        primary_window=AccountAdditionalWindow(used_percent=12.6, reset_at=1_800_000_000),
        secondary_window=AccountAdditionalWindow(used_percent=101.0, reset_at=1_800_000_100),
    )
    accounts = [_account("spark-account", additional_quotas=[quota])]
    recorded_at = {"spark-account": now}

    *_, additional = build_fleet_capacity(
        accounts,
        include_usage=True,
        generated_at=now,
        primary_recorded_at_by_account=recorded_at,
        secondary_recorded_at_by_account=recorded_at,
    )
    assert len(additional) == 1
    assert additional[0].label == "Spark"
    assert additional[0].primary_used_percent == 13
    assert additional[0].secondary_used_percent == 100

    *_, hidden = build_fleet_capacity(accounts, include_usage=False, generated_at=now)
    assert hidden == []
