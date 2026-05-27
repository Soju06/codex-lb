from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.api_keys.service import PooledCreditData, _compute_pooled_credits

pytestmark = pytest.mark.unit


def _make_account(account_id: str, plan_type: str = "plus") -> Account:
    return Account(
        id=account_id,
        chatgpt_account_id=f"workspace-{account_id}",
        email=f"{account_id}@example.com",
        plan_type=plan_type,
        access_token_encrypted=b"a",
        refresh_token_encrypted=b"b",
        id_token_encrypted=b"c",
        last_refresh=datetime.now(tz=timezone.utc),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


def _make_usage(
    account_id: str,
    window: str,
    used_percent: float,
    *,
    window_minutes: int = 300,
) -> UsageHistory:
    now = utcnow()
    return UsageHistory(
        id=abs(hash(f"{account_id}-{window}")),
        account_id=account_id,
        recorded_at=now,
        window=window,
        used_percent=used_percent,
        reset_at=int(now.timestamp()) + window_minutes * 60,
        window_minutes=window_minutes,
    )


class TestComputePooledCredits:
    def test_pools_assigned_accounts_only(self) -> None:
        acc_a = _make_account("acc-a", "plus")
        acc_b = _make_account("acc-b", "pro")
        acc_unassigned = _make_account("acc-unassigned", "pro")

        result = _compute_pooled_credits(
            assigned_account_ids=["acc-a"],
            all_accounts=[acc_a, acc_b, acc_unassigned],
            primary_usage={
                "acc-a": _make_usage("acc-a", "primary", 20.0),
                "acc-b": _make_usage("acc-b", "primary", 50.0),
                "acc-unassigned": _make_usage("acc-unassigned", "primary", 80.0),
            },
            secondary_usage={
                "acc-a": _make_usage("acc-a", "secondary", 10.0, window_minutes=10080),
                "acc-b": _make_usage("acc-b", "secondary", 30.0, window_minutes=10080),
                "acc-unassigned": _make_usage("acc-unassigned", "secondary", 60.0, window_minutes=10080),
            },
        )

        assert isinstance(result, PooledCreditData)
        assert result.remaining_percent_primary == pytest.approx(80.0)
        assert result.remaining_percent_secondary == pytest.approx(90.0)
        assert result.capacity_credits_primary == 225.0

    def test_pools_all_accounts_when_no_assignments(self) -> None:
        acc_a = _make_account("acc-a", "plus")
        acc_b = _make_account("acc-b", "pro")

        result = _compute_pooled_credits(
            assigned_account_ids=[],
            all_accounts=[acc_a, acc_b],
            primary_usage={
                "acc-a": _make_usage("acc-a", "primary", 20.0),
                "acc-b": _make_usage("acc-b", "primary", 50.0),
            },
            secondary_usage={
                "acc-a": _make_usage("acc-a", "secondary", 10.0, window_minutes=10080),
                "acc-b": _make_usage("acc-b", "secondary", 30.0, window_minutes=10080),
            },
        )

        assert result.remaining_percent_primary is not None
        assert result.remaining_percent_primary < 80.0
        assert result.remaining_percent_secondary is not None
        assert result.remaining_percent_secondary < 90.0
        assert result.capacity_credits_primary == 225.0 + 1500.0

    def test_free_tier_hides_primary_bar(self) -> None:
        acc_free = _make_account("acc-free", "free")

        result = _compute_pooled_credits(
            assigned_account_ids=["acc-free"],
            all_accounts=[acc_free],
            primary_usage={
                "acc-free": _make_usage("acc-free", "primary", 50.0),
            },
            secondary_usage={
                "acc-free": _make_usage("acc-free", "secondary", 30.0, window_minutes=10080),
            },
        )

        assert result.capacity_credits_primary == 0.0
        assert result.remaining_percent_primary is None
        assert result.remaining_percent_secondary is not None

    def test_no_accounts_returns_null_percents(self) -> None:
        result = _compute_pooled_credits(
            assigned_account_ids=[],
            all_accounts=[],
            primary_usage={},
            secondary_usage={},
        )

        assert result.remaining_percent_primary is None
        assert result.remaining_percent_secondary is None
        assert result.capacity_credits_primary == 0.0

    def test_assigned_accounts_without_usage_history_still_count_capacity(self) -> None:
        acc_a = _make_account("acc-a", "plus")

        result = _compute_pooled_credits(
            assigned_account_ids=["acc-a"],
            all_accounts=[acc_a],
            primary_usage={},
            secondary_usage={},
        )

        assert result.remaining_percent_primary == pytest.approx(100.0)
        assert result.remaining_percent_secondary == pytest.approx(100.0)
        assert result.capacity_credits_primary == 225.0

    def test_mixed_plans_sums_capacity(self) -> None:
        acc_plus = _make_account("acc-plus", "plus")
        acc_pro = _make_account("acc-pro", "pro")

        result = _compute_pooled_credits(
            assigned_account_ids=["acc-plus", "acc-pro"],
            all_accounts=[acc_plus, acc_pro],
            primary_usage={
                "acc-plus": _make_usage("acc-plus", "primary", 50.0),
                "acc-pro": _make_usage("acc-pro", "primary", 10.0),
            },
            secondary_usage={
                "acc-plus": _make_usage("acc-plus", "secondary", 50.0, window_minutes=10080),
                "acc-pro": _make_usage("acc-pro", "secondary", 10.0, window_minutes=10080),
            },
        )

        assert result.capacity_credits_primary == 225.0 + 1500.0
        assert result.remaining_percent_primary is not None
        assert result.remaining_percent_primary > 0

    def test_weekly_only_account_remapped(self) -> None:
        acc_free = _make_account("acc-free", "free")

        primary_with_weekly_minutes = _make_usage("acc-free", "primary", 50.0, window_minutes=10080)

        result = _compute_pooled_credits(
            assigned_account_ids=["acc-free"],
            all_accounts=[acc_free],
            primary_usage={"acc-free": primary_with_weekly_minutes},
            secondary_usage={},
        )

        assert result.capacity_credits_primary == 0.0
        assert result.remaining_percent_primary is None

    def test_used_percent_100_means_zero_remaining(self) -> None:
        acc_a = _make_account("acc-a", "plus")

        result = _compute_pooled_credits(
            assigned_account_ids=["acc-a"],
            all_accounts=[acc_a],
            primary_usage={"acc-a": _make_usage("acc-a", "primary", 100.0)},
            secondary_usage={"acc-a": _make_usage("acc-a", "secondary", 100.0, window_minutes=10080)},
        )

        assert result.remaining_percent_primary == pytest.approx(0.0)
        assert result.remaining_percent_secondary == pytest.approx(0.0)
