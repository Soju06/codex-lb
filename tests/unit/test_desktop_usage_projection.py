from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.core.usage.types import UsageWindowRow
from app.db.models import Account, AccountStatus, AdditionalUsageHistory
from app.modules.desktop_usage.projection import PooledUsageUnavailable, project_pool

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def account(name: str, plan: str = "plus", status: AccountStatus = AccountStatus.ACTIVE) -> Account:
    return Account(id=name, plan_type=plan, status=status)


def row(name: str, percent: float, window: str = "primary", **changes) -> UsageWindowRow:
    minutes = 300 if window == "primary" else 10080
    value = UsageWindowRow(
        account_id=name,
        used_percent=percent,
        window_minutes=minutes,
        reset_at=int(NOW.timestamp()) + minutes * 60,
        recorded_at=NOW,
    )
    return replace(value, **changes)


def project(accounts, primary, secondary, additional=None):
    return project_pool(accounts, {"primary": primary, "secondary": secondary}, additional or {}, now=NOW)


def test_weighted_quota_retains_exhausted_account_and_earliest_reset():
    payload = project(
        [account("a"), account("b", "pro")],
        [row("a", 100, reset_at=int(NOW.timestamp()) + 50), row("b", 0)],
        [row("a", 100, "secondary"), row("b", 0, "secondary")],
    )
    assert payload.rate_limit is not None
    assert payload.rate_limit.allowed
    assert payload.rate_limit.primary_window is not None
    assert payload.rate_limit.primary_window.used_percent == 13
    assert payload.rate_limit.primary_window.reset_after_seconds == 50


def test_pool_exhaustion_is_per_account_across_windows():
    payload = project(
        [account("a"), account("b")],
        [row("a", 100), row("b", 0)],
        [row("a", 0, "secondary"), row("b", 100, "secondary")],
    )
    assert payload.rate_limit is not None
    assert payload.rate_limit.limit_reached
    assert not payload.rate_limit.allowed


@pytest.mark.parametrize(
    "changes",
    [
        {"recorded_at": NOW - timedelta(seconds=180)},
        {"recorded_at": NOW + timedelta(seconds=1)},
        {"reset_at": int(NOW.timestamp())},
        {"used_percent": float("nan")},
        {"used_percent": float("inf")},
        {"used_percent": -1},
        {"used_percent": 101},
        {"window_minutes": None},
        {"reset_at": None},
        {"recorded_at": None},
    ],
)
def test_uncertain_evidence_is_unavailable(changes):
    with pytest.raises(PooledUsageUnavailable):
        project([account("a")], [row("a", 0, **changes)], [row("a", 0, "secondary")])


def test_missing_window_is_unavailable():
    with pytest.raises(PooledUsageUnavailable):
        project([account("a")], [row("a", 0)], [])


def test_unknown_plan_is_unavailable():
    with pytest.raises(PooledUsageUnavailable):
        project([account("a", "unknown")], [row("a", 0)], [row("a", 0, "secondary")])


@pytest.mark.parametrize("status", [AccountStatus.PAUSED, AccountStatus.DEACTIVATED, AccountStatus.REAUTH_REQUIRED])
def test_unavailable_identity_is_excluded(status):
    payload = project([account("a"), account("b", status=status)], [row("a", 100)], [row("a", 100, "secondary")])
    assert payload.rate_limit is not None
    assert payload.rate_limit.limit_reached


@pytest.mark.parametrize("status", [AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED])
def test_blocking_status_cannot_supply_availability(status):
    payload = project([account("a", status=status)], [row("a", 0)], [row("a", 0, "secondary")])
    assert payload.rate_limit is not None
    assert payload.rate_limit.limit_reached


def test_empty_pool_is_unavailable():
    with pytest.raises(PooledUsageUnavailable):
        project([], [], [])


def test_weekly_only_sample_normalizes_without_inventing_primary():
    payload = project([account("a", "free")], [row("a", 20, "secondary")], [])
    assert payload.rate_limit is not None
    assert payload.rate_limit.allowed
    assert payload.rate_limit.primary_window is None
    assert payload.rate_limit.secondary_window is not None
    assert payload.rate_limit.secondary_window.used_percent == 20


def test_additional_secondary_exhaustion_blocks_model():
    def entry(window, percent):
        return AdditionalUsageHistory(
            account_id="a",
            quota_key="model",
            limit_name="Model",
            metered_feature="model",
            window=window,
            used_percent=percent,
            window_minutes=300,
            recorded_at=NOW,
            reset_at=int(NOW.timestamp()) + 100,
        )

    payload = project(
        [account("a")],
        [row("a", 0)],
        [row("a", 0, "secondary")],
        {"model": {"primary": [entry("primary", 0)], "secondary": [entry("secondary", 100)]}},
    )
    assert payload.rate_limit is not None
    assert payload.rate_limit.allowed
    assert payload.additional_rate_limits[0].rate_limit is not None
    assert payload.additional_rate_limits[0].rate_limit.limit_reached


def test_missing_pool_member_cannot_be_silently_dropped():
    with pytest.raises(PooledUsageUnavailable):
        project([account("a"), account("b")], [row("a", 0)], [row("a", 0, "secondary")])


@pytest.mark.parametrize("placeholder_age", [0, 3600])
def test_weekly_primary_replaces_no_data_secondary_placeholder(placeholder_age):
    payload = project(
        [account("a", "pro")],
        [row("a", 52, "secondary")],
        [row("a", 0, reset_at=None, window_minutes=0, recorded_at=NOW - timedelta(seconds=placeholder_age))],
    )
    assert payload.rate_limit is not None
    assert payload.rate_limit.primary_window is None
    assert payload.rate_limit.secondary_window is not None
    assert payload.rate_limit.secondary_window.used_percent == 52


def test_newer_placeholder_is_unavailable_instead_of_zero_used():
    with pytest.raises(PooledUsageUnavailable):
        project(
            [account("a", "pro")],
            [row("a", 52, "secondary", recorded_at=NOW - timedelta(seconds=30))],
            [row("a", 0, reset_at=None, window_minutes=0)],
        )


def test_monthly_only_free_sample_is_supported():
    payload = project_pool(
        [account("a", "free")],
        {"monthly": [row("a", 50, window_minutes=43200)]},
        {},
        now=NOW,
    )
    assert payload.rate_limit is not None
    assert payload.rate_limit.allowed
    assert payload.rate_limit.monthly_window is not None
    assert payload.rate_limit.monthly_window.used_percent == 50


def additional_entry(name, percent, *, recorded_at=NOW, window="primary"):
    return AdditionalUsageHistory(
        account_id=name,
        quota_key="model",
        limit_name="Model",
        metered_feature="model",
        window=window,
        used_percent=percent,
        window_minutes=300,
        recorded_at=recorded_at,
        reset_at=int(NOW.timestamp()) + 100,
    )


def test_additional_same_plan_uses_observed_arithmetic_mean():
    payload = project(
        [account("a"), account("b"), account("c")],
        [row(name, 0) for name in "abc"],
        [row(name, 0, "secondary") for name in "abc"],
        {"model": {"primary": [additional_entry("a", 100), additional_entry("b", 0)]}},
    )
    assert payload.additional_rate_limits[0].rate_limit is not None
    assert payload.additional_rate_limits[0].rate_limit.primary_window is not None
    assert payload.additional_rate_limits[0].rate_limit.primary_window.used_percent == 50
    assert payload.additional_rate_limits[0].rate_limit.allowed


@pytest.mark.parametrize("same_usage", [False, True])
def test_mixed_plan_model_percentage_requires_capacity_independent_result(same_usage):
    def compute():
        return project(
            [account("a"), account("b", "pro")],
            [row(name, 0) for name in "ab"],
            [row(name, 0, "secondary") for name in "ab"],
            {"model": {"primary": [additional_entry("a", 0 if same_usage else 100), additional_entry("b", 0)]}},
        )

    if same_usage:
        payload = compute()
        assert payload.additional_rate_limits[0].rate_limit is not None
        assert payload.additional_rate_limits[0].rate_limit.primary_window is not None
        assert payload.additional_rate_limits[0].rate_limit.primary_window.used_percent == 0
    else:
        with pytest.raises(PooledUsageUnavailable):
            compute()


def test_different_model_window_durations_are_not_averaged():
    other = additional_entry("b", 0)
    other.window_minutes = 10080
    with pytest.raises(PooledUsageUnavailable):
        project(
            [account("a"), account("b")],
            [row(name, 0) for name in "ab"],
            [row(name, 0, "secondary") for name in "ab"],
            {"model": {"primary": [additional_entry("a", 0), other]}},
        )


def test_additional_stale_contributor_is_unavailable():
    with pytest.raises(PooledUsageUnavailable):
        project(
            [account("a")],
            [row("a", 0)],
            [row("a", 0, "secondary")],
            {"model": {"primary": [additional_entry("a", 0, recorded_at=NOW - timedelta(seconds=180))]}},
        )


def test_additional_missing_known_window_is_unavailable():
    with pytest.raises(PooledUsageUnavailable):
        project(
            [account("a"), account("b")],
            [row(name, 0) for name in "ab"],
            [row(name, 0, "secondary") for name in "ab"],
            {
                "model": {
                    "primary": [additional_entry("a", 0), additional_entry("b", 0)],
                    "secondary": [additional_entry("a", 0, window="secondary")],
                }
            },
        )


def test_model_availability_requires_main_capacity_on_the_same_account():
    payload = project(
        [account("a"), account("b")],
        [row("a", 100), row("b", 0)],
        [row("a", 0, "secondary"), row("b", 0, "secondary")],
        {"model": {"primary": [additional_entry("a", 0), additional_entry("b", 100)]}},
    )
    assert payload.rate_limit is not None
    assert payload.rate_limit.allowed
    assert payload.additional_rate_limits[0].rate_limit is not None
    assert not payload.additional_rate_limits[0].rate_limit.allowed


def test_original_account_reserves_do_not_contribute_pool_capacity():
    reserve = additional_entry("a", 0, recorded_at=NOW - timedelta(days=1))
    reserve.limit_name = "gpt-reserve"
    payload = project(
        [account("a")],
        [row("a", 100)],
        [row("a", 100, "secondary")],
        {"gpt_reserve": {"primary": [reserve]}},
    )
    assert payload.rate_limit is not None
    assert not payload.rate_limit.allowed
    assert payload.additional_rate_limits == []
