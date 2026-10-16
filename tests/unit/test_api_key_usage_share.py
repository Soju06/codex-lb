from datetime import datetime, timedelta, timezone

import pytest

from app.core.usage.types import UsageWindowRow
from app.db.models import Account, AccountStatus
from app.modules.api_keys.usage_share import (
    UsageShareAccount,
    UsageShareDemand,
    build_usage_share_evidence,
    estimate_usage_share,
)

NOW = datetime(2026, 9, 18, 6, 30)
NOW_EPOCH = int(NOW.replace(tzinfo=timezone.utc).timestamp())


def _account(account_id: str, plan_type: str, status: AccountStatus = AccountStatus.ACTIVE) -> Account:
    return Account(id=account_id, plan_type=plan_type, status=status)


def _row(
    account_id: str,
    *,
    window: str = "secondary",
    used_percent: float = 25.0,
    reset_in: timedelta = timedelta(days=3),
    window_minutes: int = 10_080,
    recorded_at: datetime = NOW,
) -> UsageWindowRow:
    return UsageWindowRow(
        account_id=account_id,
        used_percent=used_percent,
        reset_at=NOW_EPOCH + int(reset_in.total_seconds()),
        window_minutes=window_minutes,
        recorded_at=recorded_at,
    )


def test_other_traffic_does_not_charge_an_idle_key() -> None:
    estimate = estimate_usage_share(
        20,
        [UsageShareAccount("account", 300.0, 90.0, NOW - timedelta(days=4), NOW, NOW_EPOCH + 1)],
        {"account": UsageShareDemand(total_units=100.0, key_units=0.0)},
    )

    assert estimate.estimated_used_credits == 0
    assert estimate.allowance_credits == 60
    assert not estimate.exceeded


def test_empty_pool_does_not_turn_zero_allowance_into_a_limit_hit() -> None:
    estimate = estimate_usage_share(20, [], {})

    assert estimate.capacity_credits == 0
    assert estimate.allowance_credits == 0
    assert not estimate.exceeded


def test_equal_estimated_usage_reaches_the_allocation() -> None:
    estimate = estimate_usage_share(
        10,
        [UsageShareAccount("account", 300.0, 20.0, NOW - timedelta(days=4), NOW, NOW_EPOCH + 1)],
        {"account": UsageShareDemand(total_units=100.0, key_units=50.0)},
    )

    assert estimate.estimated_used_credits == 30
    assert estimate.allowance_credits == 30
    assert estimate.exceeded


def test_float_rounding_cannot_open_the_equality_boundary() -> None:
    estimate = estimate_usage_share(
        3,
        [UsageShareAccount("account", 1_134.0, 4.0, NOW - timedelta(days=4), NOW, NOW_EPOCH + 1)],
        {"account": UsageShareDemand(total_units=100.0, key_units=75.0)},
    )

    assert estimate.estimated_used_credits < estimate.allowance_credits
    assert estimate.estimated_used_credits == pytest.approx(34.02)
    assert estimate.exceeded


@pytest.mark.parametrize(
    "demand",
    [
        UsageShareDemand(total_units=float("nan"), key_units=100.0),
        UsageShareDemand(total_units=float("inf"), key_units=100.0),
        UsageShareDemand(total_units=100.0, key_units=float("nan")),
        UsageShareDemand(total_units=100.0, key_units=float("inf")),
    ],
)
def test_non_finite_demand_cannot_false_block_a_key(demand: UsageShareDemand) -> None:
    estimate = estimate_usage_share(
        20,
        [UsageShareAccount("account", 100.0, 100.0, NOW - timedelta(days=4), NOW, NOW_EPOCH + 1)],
        {"account": demand},
    )

    assert estimate.estimated_used_credits == 0
    assert not estimate.exceeded


def test_routing_expiry_bounds_the_cached_snapshot() -> None:
    routing_expires_at = NOW_EPOCH + 30
    estimate = estimate_usage_share(
        20,
        [
            UsageShareAccount(
                "account",
                100.0,
                10.0,
                NOW - timedelta(days=1),
                NOW,
                NOW_EPOCH + 3_600,
                evidence_expires_at=NOW_EPOCH + 180,
            )
        ],
        {},
        routing_expires_at=routing_expires_at,
    )

    assert estimate.routing_expires_at == routing_expires_at
    assert estimate.snapshot_expires_at == routing_expires_at


def test_mixed_account_capacities_are_attributed_per_account() -> None:
    estimate = estimate_usage_share(
        10,
        [
            UsageShareAccount("plus", 7_560.0, 50.0, NOW - timedelta(days=4), NOW, NOW_EPOCH + 1),
            UsageShareAccount("pro", 50_400.0, 10.0, NOW - timedelta(days=4), NOW, NOW_EPOCH + 2),
        ],
        {
            "plus": UsageShareDemand(total_units=100.0, key_units=25.0),
            "pro": UsageShareDemand(total_units=100.0, key_units=50.0),
        },
    )

    assert estimate.capacity_credits == 57_960
    assert estimate.estimated_used_credits == 3_465
    assert estimate.allowance_credits == 5_796
    assert not estimate.exceeded


def test_paid_and_monthly_only_accounts_resolve_their_current_long_windows() -> None:
    evidence = build_usage_share_evidence(
        [_account("plus", "plus"), _account("free", "free")],
        primary_rows=[],
        secondary_rows=[_row("plus", used_percent=40)],
        monthly_rows=[
            _row(
                "free",
                window="monthly",
                used_percent=30,
                reset_in=timedelta(days=20),
                window_minutes=43_200,
            )
        ],
        now=NOW,
    )

    assert evidence.unavailable_account_ids == ()
    by_id = {account.account_id: account for account in evidence.accounts}
    assert by_id["plus"].capacity_credits == 7_560
    assert by_id["free"].capacity_credits == 1_134
    assert by_id["plus"].used_percent == 40
    assert by_id["free"].used_percent == 30


def test_monthly_only_plan_does_not_fall_back_to_a_weekly_row() -> None:
    evidence = build_usage_share_evidence(
        [_account("free", "free")],
        primary_rows=[],
        secondary_rows=[_row("free", used_percent=20)],
        monthly_rows=[],
        now=NOW,
    )

    assert evidence.accounts == ()
    assert evidence.unavailable_account_ids == ("free",)


def test_weekly_window_reported_in_primary_slot_is_normalized() -> None:
    evidence = build_usage_share_evidence(
        [_account("free", "free")],
        primary_rows=[_row("free", window="primary", window_minutes=10_080)],
        secondary_rows=[],
        monthly_rows=[],
        now=NOW,
    )

    assert evidence.unavailable_account_ids == ()
    assert evidence.accounts[0].capacity_credits == 1_134


def test_fresh_weekly_primary_supersedes_stale_monthly_residue() -> None:
    evidence = build_usage_share_evidence(
        [_account("free", "free")],
        primary_rows=[_row("free", window="primary", used_percent=20, window_minutes=10_080)],
        secondary_rows=[],
        monthly_rows=[
            _row(
                "free",
                window="monthly",
                used_percent=100,
                reset_in=timedelta(days=20),
                window_minutes=43_200,
                recorded_at=NOW - timedelta(hours=1),
            )
        ],
        now=NOW,
    )

    assert evidence.unavailable_account_ids == ()
    assert evidence.accounts[0].used_percent == 20
    assert evidence.accounts[0].window_started_at == NOW + timedelta(days=3) - timedelta(days=7)


def test_later_weekly_primary_supersedes_still_fresh_monthly_residue() -> None:
    evidence = build_usage_share_evidence(
        [_account("free", "free")],
        primary_rows=[_row("free", window="primary", used_percent=20, window_minutes=10_080)],
        secondary_rows=[],
        monthly_rows=[
            _row(
                "free",
                window="monthly",
                used_percent=80,
                reset_in=timedelta(days=20),
                window_minutes=43_200,
                recorded_at=NOW - timedelta(minutes=1),
            )
        ],
        now=NOW,
    )

    assert evidence.unavailable_account_ids == ()
    assert evidence.accounts[0].used_percent == 20
    assert evidence.accounts[0].window_started_at == NOW + timedelta(days=3) - timedelta(days=7)


def test_later_monthly_row_supersedes_weekly_primary_residue() -> None:
    evidence = build_usage_share_evidence(
        [_account("free", "free")],
        primary_rows=[
            _row(
                "free",
                window="primary",
                used_percent=80,
                window_minutes=10_080,
                recorded_at=NOW - timedelta(minutes=1),
            )
        ],
        secondary_rows=[],
        monthly_rows=[
            _row(
                "free",
                window="monthly",
                used_percent=20,
                reset_in=timedelta(days=20),
                window_minutes=43_200,
            )
        ],
        now=NOW,
    )

    assert evidence.unavailable_account_ids == ()
    assert evidence.accounts[0].used_percent == 20
    assert evidence.accounts[0].window_started_at == NOW + timedelta(days=20) - timedelta(days=30)


@pytest.mark.parametrize(
    ("plan_type", "window", "window_minutes", "reset_in"),
    [
        ("plus", "secondary", 300, timedelta(hours=2)),
        ("free", "monthly", 10_080, timedelta(days=3)),
    ],
)
def test_noncanonical_long_window_duration_marks_evidence_unavailable(
    plan_type: str,
    window: str,
    window_minutes: int,
    reset_in: timedelta,
) -> None:
    row = _row(
        "account",
        window=window,
        window_minutes=window_minutes,
        reset_in=reset_in,
    )
    evidence = build_usage_share_evidence(
        [_account("account", plan_type)],
        primary_rows=[],
        secondary_rows=[row] if window == "secondary" else [],
        monthly_rows=[row] if window == "monthly" else [],
        now=NOW,
    )

    assert evidence.accounts == ()
    assert evidence.unavailable_account_ids == ("account",)


def test_future_dated_evidence_marks_the_estimate_unavailable() -> None:
    evidence = build_usage_share_evidence(
        [_account("future", "plus")],
        primary_rows=[],
        secondary_rows=[_row("future", recorded_at=NOW + timedelta(seconds=1))],
        monthly_rows=[],
        now=NOW,
    )

    assert evidence.accounts == ()
    assert evidence.unavailable_account_ids == ("future",)


def test_evidence_expiry_tracks_the_freshness_horizon() -> None:
    evidence = build_usage_share_evidence(
        [_account("account", "plus")],
        primary_rows=[],
        secondary_rows=[_row("account", recorded_at=NOW - timedelta(seconds=1))],
        monthly_rows=[],
        now=NOW,
    )

    account = evidence.accounts[0]
    assert account.observed_at == NOW - timedelta(seconds=1)
    assert account.evidence_expires_at == NOW_EPOCH + 179
    estimate = estimate_usage_share(20, evidence.accounts, {})
    assert estimate.evidence_expires_at == account.evidence_expires_at
    assert estimate.snapshot_expires_at == account.evidence_expires_at


def test_evidence_at_the_freshness_boundary_is_unavailable() -> None:
    evidence = build_usage_share_evidence(
        [_account("boundary", "plus")],
        primary_rows=[],
        secondary_rows=[_row("boundary", recorded_at=NOW - timedelta(seconds=180))],
        monthly_rows=[],
        now=NOW,
    )

    assert evidence.accounts == ()
    assert evidence.unavailable_account_ids == ("boundary",)


def test_missing_or_stale_evidence_marks_the_estimate_unavailable() -> None:
    evidence = build_usage_share_evidence(
        [_account("missing", "plus"), _account("stale", "plus")],
        primary_rows=[],
        secondary_rows=[_row("stale", recorded_at=NOW - timedelta(hours=1))],
        monthly_rows=[],
        now=NOW,
    )

    assert evidence.accounts == ()
    assert evidence.unavailable_account_ids == ("missing", "stale")


def test_delete_pending_account_does_not_contribute_capacity_or_unavailability() -> None:
    account = _account("account", "plus")
    account.delete_requested_at = NOW

    evidence = build_usage_share_evidence(
        [account],
        primary_rows=[],
        secondary_rows=[],
        monthly_rows=[],
        now=NOW,
    )

    assert evidence.accounts == ()
    assert evidence.unavailable_account_ids == ()


@pytest.mark.parametrize("status", [AccountStatus.PAUSED, AccountStatus.DEACTIVATED])
def test_unroutable_accounts_do_not_contribute_capacity_or_unavailability(status: AccountStatus) -> None:
    evidence = build_usage_share_evidence(
        [_account("account", "plus", status)],
        primary_rows=[],
        secondary_rows=[],
        monthly_rows=[],
        now=NOW,
    )

    assert evidence.accounts == ()
    assert evidence.unavailable_account_ids == ()


def test_impossible_window_range_marks_evidence_unavailable() -> None:
    evidence = build_usage_share_evidence(
        [_account("account", "plus")],
        primary_rows=[],
        secondary_rows=[_row("account", window_minutes=10**100)],
        monthly_rows=[],
        now=NOW,
    )

    assert evidence.accounts == ()
    assert evidence.unavailable_account_ids == ("account",)


def test_sample_predating_its_reported_window_marks_evidence_unavailable() -> None:
    evidence = build_usage_share_evidence(
        [_account("account", "plus")],
        primary_rows=[],
        secondary_rows=[
            _row(
                "account",
                reset_in=timedelta(days=6, hours=23, minutes=59),
                window_minutes=10_080,
                recorded_at=NOW - timedelta(minutes=2),
            )
        ],
        monthly_rows=[],
        now=NOW,
    )

    assert evidence.accounts == ()
    assert evidence.unavailable_account_ids == ("account",)


def test_window_start_is_derived_from_the_account_reset() -> None:
    evidence = build_usage_share_evidence(
        [_account("account", "plus")],
        primary_rows=[],
        secondary_rows=[_row("account", reset_in=timedelta(days=2), window_minutes=10_080)],
        monthly_rows=[],
        now=NOW,
    )

    account = evidence.accounts[0]
    assert account.window_started_at == NOW + timedelta(days=2) - timedelta(days=7)
