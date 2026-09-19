from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.config.settings import Settings
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, AdditionalUsageHistory, UsageHistory
from app.modules.accounts import mappers, reset_credit_cache
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


def test_account_to_summary_surfaces_owner_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mappers.config_settings, "get_settings", lambda: Settings(local_instance_id="studio"))
    encryptor = TokenEncryptor()

    owned = _account()
    owned.provider = "openai"
    owned.owner_instance = None
    mirrored = _account()
    mirrored.provider = "openai"
    mirrored.owner_instance = "other-instance"

    owned_summary = mappers._account_to_summary(
        owned, None, None, None, None, None, None, None, encryptor, include_auth=False
    )
    mirrored_summary = mappers._account_to_summary(
        mirrored, None, None, None, None, None, None, None, encryptor, include_auth=False
    )

    assert owned_summary.owner_instance is None
    assert owned_summary.is_locally_owned is True
    assert mirrored_summary.owner_instance == "other-instance"
    assert mirrored_summary.is_locally_owned is False


def test_account_to_summary_exposes_cached_reset_credits_for_openai_only() -> None:
    encryptor = TokenEncryptor()
    openai = _account(AccountStatus.ACTIVE)
    openai.provider = "OpenAI"
    anthropic = _account(AccountStatus.ACTIVE)
    anthropic.id = "account-2"
    anthropic.provider = "anthropic"
    reset_credit_cache.reset()
    reset_credit_cache.record_count(openai.id, 2)
    reset_credit_cache.record_count(anthropic.id, 4)
    try:
        openai_summary = mappers._account_to_summary(
            openai, None, None, None, None, None, None, None, encryptor, include_auth=False
        )
        anthropic_summary = mappers._account_to_summary(
            anthropic, None, None, None, None, None, None, None, encryptor, include_auth=False
        )

        assert openai_summary.reset_credits_available == 2
        assert openai_summary.model_dump(by_alias=True)["resetCreditsAvailable"] == 2
        assert anthropic_summary.reset_credits_available is None

        reset_credit_cache.clear(openai.id)
        unknown_summary = mappers._account_to_summary(
            openai, None, None, None, None, None, None, None, encryptor, include_auth=False
        )
        assert unknown_summary.reset_credits_available is None
    finally:
        reset_credit_cache.reset()


def test_account_to_summary_openai_without_primary_usage_has_no_primary_gauge() -> None:
    # OpenAI removed the codex 5h limit: no primary usage must not default to
    # a full 100% primary gauge for openai accounts.
    encryptor = TokenEncryptor()
    openai = _account(AccountStatus.ACTIVE)
    openai.provider = "openai"
    summary = mappers._account_to_summary(
        openai, None, None, None, None, None, None, None, encryptor, include_auth=False
    )
    assert summary.usage.primary_remaining_percent is None
    assert summary.usage.secondary_remaining_percent is None


def test_normalize_account_routing_policy() -> None:
    assert _normalize_account_routing_policy("normal") == "normal"
    assert _normalize_account_routing_policy("burn_first") == "burn_first"
    assert _normalize_account_routing_policy("preserve") == "preserve"
    assert _normalize_account_routing_policy("legacy") == "normal"
    assert _normalize_account_routing_policy(None) == "normal"


def test_fable_eligible_flag_by_provider_and_threshold() -> None:
    anthropic = _account(AccountStatus.ACTIVE)
    anthropic.provider = "anthropic"
    openai = _account(AccountStatus.ACTIVE)
    openai.provider = "openai"

    # Anthropic: under threshold eligible, at/over threshold not, no sample eligible.
    assert mappers._fable_eligible(anthropic, 10.0) is True
    assert mappers._fable_eligible(anthropic, 50.0) is False
    assert mappers._fable_eligible(anthropic, 90.0) is False
    assert mappers._fable_eligible(anthropic, None) is True
    # Other providers: null.
    assert mappers._fable_eligible(openai, 10.0) is None


@pytest.mark.parametrize(
    "status",
    [AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED, AccountStatus.PAUSED],
)
def test_fable_eligible_false_for_non_routable_statuses(status: AccountStatus) -> None:
    anthropic = _account(status)
    anthropic.provider = "anthropic"

    assert mappers._fable_eligible(anthropic, 10.0) is False


def test_fable_eligible_false_for_canceled_subscription() -> None:
    anthropic = _account(AccountStatus.ACTIVE)
    anthropic.provider = "anthropic"
    anthropic.subscription_status = "canceled"

    assert mappers._fable_eligible(anthropic, 10.0) is False


def _fable_scoped_weekly(*, used_percent: float, recorded_at: datetime) -> AdditionalUsageHistory:
    return AdditionalUsageHistory(
        account_id="account-1",
        quota_key="anthropic_fable_scoped_weekly",
        limit_name="anthropic_fable_scoped_weekly",
        metered_feature="anthropic_fable_scoped_weekly",
        window="primary",
        used_percent=used_percent,
        recorded_at=recorded_at,
    )


def test_fable_eligible_prefers_fresh_scoped_signal_over_heuristic() -> None:
    anthropic = _account(AccountStatus.ACTIVE)
    anthropic.provider = "anthropic"
    now = datetime.now(timezone.utc)

    # Overall weekly (62%) is over the heuristic threshold, but a fresh
    # scoped entry with headroom (45%) overrides it.
    fresh_headroom = _fable_scoped_weekly(used_percent=45.0, recorded_at=now)
    assert mappers._fable_eligible(anthropic, 62.0, fresh_headroom) is True

    # Overall weekly (30%) is well under the heuristic, but a fresh scoped
    # entry at the scoped threshold (100%) overrides it the other way.
    fresh_exhausted = _fable_scoped_weekly(used_percent=100.0, recorded_at=now)
    assert mappers._fable_eligible(anthropic, 30.0, fresh_exhausted) is False

    # A stale (>6h) scoped entry is ignored; the heuristic applies.
    stale_exhausted = _fable_scoped_weekly(used_percent=100.0, recorded_at=now - timedelta(hours=7))
    assert mappers._fable_eligible(anthropic, 30.0, stale_exhausted) is True

    # No scoped entry at all: unchanged heuristic behavior.
    assert mappers._fable_eligible(anthropic, 62.0, None) is False


def test_scoped_threshold_reserves_the_last_tenth_of_the_fable_week(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scoped default stops routed Fable traffic at 90% used, leaving the
    rest of the vendor window for interactive work.

    Serialization of this same row onto the account payload is covered in
    tests/unit/test_account_fable_scoped_summary.py.
    """
    monkeypatch.setattr(mappers.config_settings, "get_settings", Settings)
    anthropic = _account(AccountStatus.ACTIVE)
    anthropic.provider = "anthropic"
    now = datetime.now(timezone.utc)

    assert Settings().anthropic_fable_scoped_max_used_percent == 90.0
    assert mappers._fable_eligible(anthropic, 10.0, _fable_scoped_weekly(used_percent=89.0, recorded_at=now)) is True
    assert mappers._fable_eligible(anthropic, 10.0, _fable_scoped_weekly(used_percent=95.0, recorded_at=now)) is False


def test_effective_status_ignores_extra_usage_credits_for_anthropic_accounts() -> None:
    """Anthropic extra-usage credits stay dashboard-visible but must not
    rescue an exhausted account's effective status."""
    account = _account()
    account.provider = "anthropic"
    primary = _primary_usage()
    secondary = _secondary_usage(
        credits_has=True,
        credits_unlimited=False,
        credits_balance=186.6,
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
        == AccountStatus.QUOTA_EXCEEDED
    )
