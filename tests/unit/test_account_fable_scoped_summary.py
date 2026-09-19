from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.config.settings import Settings
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, AdditionalUsageHistory
from app.modules.accounts import mappers


def _account(provider: str = "anthropic") -> Account:
    return Account(
        id="account-1",
        provider=provider,
        email="account@example.com",
        plan_type="max",
        access_token_encrypted=b"",
        refresh_token_encrypted=b"",
        id_token_encrypted=b"",
        status=AccountStatus.ACTIVE,
    )


def _marker(*, used_percent: float, recorded_at: datetime, reset_at: int | None = None) -> AdditionalUsageHistory:
    return AdditionalUsageHistory(
        account_id="account-1",
        quota_key="anthropic_fable_scoped_weekly",
        limit_name="anthropic_fable_scoped_weekly",
        metered_feature="anthropic_fable_scoped_weekly",
        window="primary",
        used_percent=used_percent,
        reset_at=reset_at,
        window_minutes=10080,
        recorded_at=recorded_at,
    )


def _summary(account: Account, marker: AdditionalUsageHistory | None):
    return mappers._account_to_summary(
        account,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        TokenEncryptor(),
        include_auth=False,
        fable_scoped_weekly=marker,
    )


def test_fresh_marker_is_serialized_with_reset_and_freshness() -> None:
    now = datetime.now(timezone.utc)
    reset_epoch = 1_800_000_000

    summary = _summary(_account(), _marker(used_percent=87.0, recorded_at=now, reset_at=reset_epoch))

    assert summary.fable_scoped_weekly is not None
    assert summary.fable_scoped_weekly.used_percent == 87.0
    assert summary.fable_scoped_weekly.fresh is True
    assert summary.fable_scoped_weekly.reset_at == datetime.fromtimestamp(reset_epoch, tz=timezone.utc)
    assert summary.fable_scoped_weekly.recorded_at == now


def test_stale_marker_is_still_serialized_but_not_fresh() -> None:
    now = datetime.now(timezone.utc)

    summary = _summary(_account(), _marker(used_percent=100.0, recorded_at=now - timedelta(hours=7)))

    assert summary.fable_scoped_weekly is not None
    assert summary.fable_scoped_weekly.used_percent == 100.0
    assert summary.fable_scoped_weekly.fresh is False
    # Stale means routing falls back to the weekly heuristic, so the boolean
    # and the marker disagree on purpose.
    assert summary.fable_eligible is True


def test_marker_without_reset_at_serializes_null_reset() -> None:
    summary = _summary(_account(), _marker(used_percent=6.0, recorded_at=datetime.now(timezone.utc)))

    assert summary.fable_scoped_weekly is not None
    assert summary.fable_scoped_weekly.reset_at is None


def test_no_marker_is_null() -> None:
    assert _summary(_account(), None).fable_scoped_weekly is None


def test_non_anthropic_account_is_null_even_with_a_marker() -> None:
    marker = _marker(used_percent=10.0, recorded_at=datetime.now(timezone.utc))

    assert _summary(_account(provider="openai"), marker).fable_scoped_weekly is None


def test_scoped_threshold_reserves_the_last_tenth_of_the_fable_week() -> None:
    assert Settings().anthropic_fable_scoped_max_used_percent == 90.0


def test_account_past_the_reserve_is_not_fable_eligible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mappers.config_settings, "get_settings", Settings)
    now = datetime.now(timezone.utc)

    inside = _summary(_account(), _marker(used_percent=89.0, recorded_at=now))
    past = _summary(_account(), _marker(used_percent=95.0, recorded_at=now))

    assert inside.fable_eligible is True
    assert past.fable_eligible is False


def test_wire_shape_is_camel_case() -> None:
    now = datetime.now(timezone.utc)
    summary = _summary(_account(), _marker(used_percent=42.5, recorded_at=now, reset_at=1_800_000_000))

    payload = summary.model_dump(mode="json", by_alias=True)["fableScopedWeekly"]

    assert set(payload) == {"usedPercent", "resetAt", "recordedAt", "fresh"}
    assert payload["usedPercent"] == 42.5
    assert payload["fresh"] is True
