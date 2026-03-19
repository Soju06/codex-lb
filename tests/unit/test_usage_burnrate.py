from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.crypto import TokenEncryptor
from app.core.utils.time import naive_utc_to_epoch, utcnow
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.usage.burnrate import compute_burn_rate_snapshot

pytestmark = pytest.mark.unit


def _make_account(account_id: str, plan_type: str, status: AccountStatus = AccountStatus.ACTIVE) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=f"{account_id}@example.com",
        plan_type=plan_type,
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=status,
        deactivation_reason=None,
    )


def _usage(account_id: str, used_percent: float, *, window_minutes: int, reset_at: int, window: str) -> UsageHistory:
    return UsageHistory(
        account_id=account_id,
        used_percent=used_percent,
        window=window,
        window_minutes=window_minutes,
        reset_at=reset_at,
        recorded_at=utcnow(),
    )


def test_burnrate_unknown_plan_defaults_to_plus_equivalent_weight() -> None:
    now = utcnow().replace(microsecond=0)
    reset_epoch = int(naive_utc_to_epoch(now))

    account = _make_account("acc-enterprise", "enterprise")
    primary = _usage(account.id, 50.0, window_minutes=300, reset_at=reset_epoch, window="primary")
    secondary = _usage(account.id, 20.0, window_minutes=10080, reset_at=reset_epoch, window="secondary")

    snapshot = compute_burn_rate_snapshot(
        accounts=[account],
        latest_primary_usage={account.id: primary},
        latest_secondary_usage={account.id: secondary},
        now=now,
    )

    assert snapshot.primary.projected_plus_accounts == pytest.approx(0.5)
    assert snapshot.secondary.projected_plus_accounts == pytest.approx(0.2)
    assert snapshot.primary.max_plus_equivalent_accounts == pytest.approx(1.0)
    assert snapshot.secondary.max_plus_equivalent_accounts == pytest.approx(1.0)


def test_burnrate_known_pro_plan_uses_plus_capacity_ratio() -> None:
    now = utcnow().replace(microsecond=0)
    reset_epoch = int(naive_utc_to_epoch(now))

    account = _make_account("acc-pro", "pro")
    primary = _usage(account.id, 50.0, window_minutes=300, reset_at=reset_epoch, window="primary")
    secondary = _usage(account.id, 50.0, window_minutes=10080, reset_at=reset_epoch, window="secondary")

    snapshot = compute_burn_rate_snapshot(
        accounts=[account],
        latest_primary_usage={account.id: primary},
        latest_secondary_usage={account.id: secondary},
        now=now,
    )

    assert snapshot.primary.projected_plus_accounts == pytest.approx(1500 / 225 * 0.5)
    assert snapshot.secondary.projected_plus_accounts == pytest.approx(50400 / 7560 * 0.5)


def test_burnrate_secondary_quota_exceeded_is_counted_as_fully_burned() -> None:
    now = utcnow().replace(microsecond=0)
    reset_epoch = int(naive_utc_to_epoch(now + timedelta(days=1)))

    account = _make_account("acc-plus", "plus", status=AccountStatus.QUOTA_EXCEEDED)
    secondary = _usage(account.id, 0.0, window_minutes=10080, reset_at=reset_epoch, window="secondary")

    snapshot = compute_burn_rate_snapshot(
        accounts=[account],
        latest_primary_usage={},
        latest_secondary_usage={account.id: secondary},
        now=now,
    )

    assert snapshot.secondary.used_plus_accounts == pytest.approx(1.0)
    assert snapshot.secondary.projected_plus_accounts == pytest.approx(1.0)


def test_burnrate_normalizes_weekly_only_primary_rows_into_secondary() -> None:
    now = utcnow().replace(microsecond=0)
    reset_epoch = int(naive_utc_to_epoch(now))

    account = _make_account("acc-weekly", "free")
    weekly_primary = _usage(account.id, 20.0, window_minutes=10080, reset_at=reset_epoch, window="primary")

    snapshot = compute_burn_rate_snapshot(
        accounts=[account],
        latest_primary_usage={account.id: weekly_primary},
        latest_secondary_usage={},
        now=now,
    )

    assert snapshot.primary.projected_plus_accounts is None
    assert snapshot.secondary.projected_plus_accounts == pytest.approx(0.2)
