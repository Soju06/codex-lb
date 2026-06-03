from __future__ import annotations

from datetime import datetime

from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.accounts import mappers
from app.modules.accounts.mappers import _effective_status_from_usage, _normalize_account_routing_policy


def _usage(
    *,
    recorded_at: datetime,
    credits_has: bool,
    credits_balance: float,
) -> UsageHistory:
    return UsageHistory(
        account_id="acc",
        recorded_at=recorded_at,
        window="secondary",
        used_percent=100.0,
        credits_has=credits_has,
        credits_unlimited=False,
        credits_balance=credits_balance,
    )


def test_extract_credit_status_uses_freshest_sample() -> None:
    stale = _usage(
        recorded_at=datetime(2026, 1, 1, 12, 0, 0),
        credits_has=True,
        credits_balance=10.0,
    )
    fresh = _usage(
        recorded_at=datetime(2026, 1, 1, 12, 1, 0),
        credits_has=False,
        credits_balance=0.0,
    )

    assert mappers._extract_credit_status(stale, fresh) == (False, False, 0.0)


def _account(status: AccountStatus = AccountStatus.QUOTA_EXCEEDED) -> Account:
    return Account(
        id="account-1",
        email="account@example.com",
        plan_type="plus",
        access_token_encrypted=b"",
        refresh_token_encrypted=b"",
        id_token_encrypted=b"",
        status=status,
        reset_at=1_700_003_600,
    )


def _primary_usage(**overrides) -> UsageHistory:
    values = {
        "account_id": "account-1",
        "window": "primary",
        "used_percent": 40.0,
        "reset_at": None,
        "window_minutes": 300,
    }
    values.update(overrides)
    return UsageHistory(**values)


def _secondary_usage(**overrides) -> UsageHistory:
    values = {
        "account_id": "account-1",
        "window": "secondary",
        "used_percent": 100.0,
        "reset_at": 1_700_003_600,
        "window_minutes": 10080,
    }
    values.update(overrides)
    return UsageHistory(**values)


def test_effective_status_uses_secondary_credits_to_reactivate_quota_exceeded_account() -> None:
    account = _account()
    primary = _primary_usage()
    secondary = _secondary_usage(
        credits_has=False,
        credits_unlimited=False,
        credits_balance=25.0,
    )

    assert (
        _effective_status_from_usage(
            account,
            status_seed=account.status,
            primary_usage=primary,
            primary_used_percent=primary.used_percent,
            secondary_usage=secondary,
            secondary_used_percent=secondary.used_percent,
            monthly_usage=None,
            monthly_used_percent=None,
            runtime_reset=float(account.reset_at) if account.reset_at else None,
        )
        == AccountStatus.ACTIVE
    )


def test_effective_status_uses_primary_credits_when_secondary_has_no_credit_fields() -> None:
    account = _account()
    primary = _primary_usage(credits_balance=25.0)
    secondary = _secondary_usage()

    assert (
        _effective_status_from_usage(
            account,
            status_seed=account.status,
            primary_usage=primary,
            primary_used_percent=primary.used_percent,
            secondary_usage=secondary,
            secondary_used_percent=secondary.used_percent,
            monthly_usage=None,
            monthly_used_percent=None,
            runtime_reset=float(account.reset_at) if account.reset_at else None,
        )
        == AccountStatus.ACTIVE
    )


def test_effective_status_keeps_primary_exhaustion_rate_limited_with_credits() -> None:
    account = _account(AccountStatus.ACTIVE)
    primary = _primary_usage(used_percent=100.0, reset_at=1_700_000_300, credits_balance=25.0)
    secondary = _secondary_usage(used_percent=100.0, credits_balance=25.0)

    assert (
        _effective_status_from_usage(
            account,
            status_seed=account.status,
            primary_usage=primary,
            primary_used_percent=primary.used_percent,
            secondary_usage=secondary,
            secondary_used_percent=secondary.used_percent,
            monthly_usage=None,
            monthly_used_percent=None,
            runtime_reset=float(account.reset_at) if account.reset_at else None,
        )
        == AccountStatus.RATE_LIMITED
    )


def test_effective_status_keeps_paused_account_paused_with_usable_credits() -> None:
    account = _account(AccountStatus.PAUSED)
    primary = _primary_usage(credits_balance=25.0)
    secondary = _secondary_usage(credits_balance=25.0)

    assert (
        _effective_status_from_usage(
            account,
            status_seed=account.status,
            primary_usage=primary,
            primary_used_percent=primary.used_percent,
            secondary_usage=secondary,
            secondary_used_percent=secondary.used_percent,
            monthly_usage=None,
            monthly_used_percent=None,
            runtime_reset=float(account.reset_at) if account.reset_at else None,
        )
        == AccountStatus.PAUSED
    )


def test_normalize_account_routing_policy() -> None:
    assert _normalize_account_routing_policy("normal") == "normal"
    assert _normalize_account_routing_policy("burn_first") == "burn_first"
    assert _normalize_account_routing_policy("preserve") == "preserve"
    assert _normalize_account_routing_policy("legacy") == "normal"
    assert _normalize_account_routing_policy(None) == "normal"
