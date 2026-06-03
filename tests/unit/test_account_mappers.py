from __future__ import annotations

from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.accounts.mappers import _effective_status_from_usage


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
            runtime_reset=float(account.reset_at) if account.reset_at else None,
        )
        == AccountStatus.ACTIVE
    )


def test_effective_status_keeps_primary_rate_limit_precedence_with_usable_credits() -> None:
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
            runtime_reset=float(account.reset_at) if account.reset_at else None,
        )
        == AccountStatus.PAUSED
    )
