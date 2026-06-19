from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.clients.rate_limit_reset import ConsumeRateLimitResetCode, ConsumeRateLimitResetResponse
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus
from app.modules.accounts.service import (
    AccountNotResetApplicableError,
    AccountUsageResetNoCreditError,
    AccountUsageResetRejectedError,
    AccountsService,
)

pytestmark = pytest.mark.unit

_ACCOUNT_ID = "acc_test"
_CHATGPT_ACCOUNT_ID = "chatgpt-acc-1"
_RESET_TOKEN_PLAINTEXT = "test-access-token-not-a-real-secret"


def _make_account(status: AccountStatus = AccountStatus.ACTIVE) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=_ACCOUNT_ID,
        chatgpt_account_id=_CHATGPT_ACCOUNT_ID,
        email="reset@example.com",
        plan_type="pro",
        access_token_encrypted=encryptor.encrypt(_RESET_TOKEN_PLAINTEXT),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=datetime(2026, 5, 17),
        status=status,
        deactivation_reason=None,
    )


def _make_usage_row(
    used_percent: float,
    account_id: str = _ACCOUNT_ID,
    *,
    rate_limit_reset_available_count: int | None = None,
) -> Any:
    return SimpleNamespace(
        used_percent=used_percent,
        account_id=account_id,
        rate_limit_reset_available_count=rate_limit_reset_available_count,
    )


def _build_service(
    account: Account | None,
    *,
    primary_pct: float | None = None,
    secondary_pct: float | None = None,
    reset_count: int | None = None,
    auth_manager: Any | None = None,
) -> AccountsService:
    repo = AsyncMock()
    repo.get_by_id.return_value = account

    usage_repo = AsyncMock()
    primary_entry = _make_usage_row(primary_pct, rate_limit_reset_available_count=reset_count) if primary_pct is not None else None
    secondary_entry = _make_usage_row(secondary_pct) if secondary_pct is not None else None

    async def _latest_entry_for_account(requested_account_id: str, *, window: str) -> Any:
        if requested_account_id != _ACCOUNT_ID:
            return None
        return primary_entry if window == "primary" else secondary_entry

    usage_repo.latest_entry_for_account.side_effect = _latest_entry_for_account

    service = AccountsService(repo=repo, usage_repo=usage_repo, auth_manager=auth_manager)
    usage_updater = AsyncMock()
    usage_updater.force_refresh = AsyncMock(return_value=True)
    service._usage_updater = usage_updater
    return service


@pytest.mark.asyncio
async def test_apply_usage_reset_returns_none_for_missing_account():
    service = _build_service(account=None)
    result = await service.apply_usage_reset("missing")
    assert result is None


@pytest.mark.asyncio
async def test_apply_usage_reset_rejects_paused_account():
    account = _make_account(status=AccountStatus.PAUSED)
    service = _build_service(account=account, primary_pct=100.0, reset_count=1)
    with pytest.raises(AccountNotResetApplicableError):
        await service.apply_usage_reset(_ACCOUNT_ID)


@pytest.mark.asyncio
async def test_apply_usage_reset_rejects_deactivated_account():
    account = _make_account(status=AccountStatus.DEACTIVATED)
    service = _build_service(account=account, primary_pct=100.0, reset_count=1)
    with pytest.raises(AccountNotResetApplicableError):
        await service.apply_usage_reset(_ACCOUNT_ID)


@pytest.mark.asyncio
async def test_apply_usage_reset_rejects_when_no_saved_credits():
    account = _make_account(status=AccountStatus.RATE_LIMITED)
    service = _build_service(account=account, primary_pct=100.0, reset_count=0)
    with pytest.raises(AccountUsageResetNoCreditError):
        await service.apply_usage_reset(_ACCOUNT_ID)


@pytest.mark.asyncio
async def test_apply_usage_reset_rejects_when_reset_count_unknown():
    account = _make_account(status=AccountStatus.RATE_LIMITED)
    service = _build_service(account=account, primary_pct=100.0, reset_count=None)
    with pytest.raises(AccountUsageResetNoCreditError):
        await service.apply_usage_reset(_ACCOUNT_ID)


@pytest.mark.asyncio
async def test_apply_usage_reset_captures_before_after_snapshot(monkeypatch):
    account = _make_account(status=AccountStatus.RATE_LIMITED)
    service = _build_service(
        account=account,
        primary_pct=100.0,
        secondary_pct=80.0,
        reset_count=1,
    )

    captured_kwargs: dict[str, Any] = {}

    async def _fake_consume(**kwargs):
        captured_kwargs.update(kwargs)
        return ConsumeRateLimitResetResponse(code=ConsumeRateLimitResetCode.RESET, windows_reset=2)

    monkeypatch.setattr(service, "_send_usage_reset_consume", _fake_consume)

    result = await service.apply_usage_reset(_ACCOUNT_ID)

    assert result is not None
    assert result.status == "applied"
    assert result.account_id == _ACCOUNT_ID
    assert result.consume_code == "reset"
    assert result.windows_reset == 2
    assert result.rate_limit_reset_available_count_before == 1
    assert result.rate_limit_reset_available_count_after == 1
    assert result.primary_used_percent_before == 100.0
    assert result.primary_used_percent_after == 100.0
    assert result.secondary_used_percent_before == 80.0
    assert result.secondary_used_percent_after == 80.0
    assert result.account_status_before == "rate_limited"
    assert result.account_status_after == "rate_limited"

    assert captured_kwargs["access_token"] == _RESET_TOKEN_PLAINTEXT
    assert captured_kwargs["chatgpt_account_id"] == _CHATGPT_ACCOUNT_ID

    force_refresh_mock = service._usage_updater.force_refresh
    assert isinstance(force_refresh_mock, AsyncMock)
    force_refresh_mock.assert_awaited_once_with(account)


@pytest.mark.asyncio
async def test_apply_usage_reset_refreshes_token_before_consuming(monkeypatch):
    stale_account = _make_account(status=AccountStatus.ACTIVE)
    fresh_account = _make_account(status=AccountStatus.ACTIVE)
    encryptor = TokenEncryptor()
    fresh_account.access_token_encrypted = encryptor.encrypt("fresh-access-token")
    auth_manager = SimpleNamespace(ensure_fresh=AsyncMock(return_value=fresh_account))
    service = _build_service(
        account=stale_account,
        primary_pct=95.0,
        secondary_pct=80.0,
        reset_count=1,
        auth_manager=auth_manager,
    )

    captured_kwargs: dict[str, Any] = {}

    async def _fake_consume(**kwargs):
        captured_kwargs.update(kwargs)
        return ConsumeRateLimitResetResponse(code=ConsumeRateLimitResetCode.RESET, windows_reset=1)

    monkeypatch.setattr(service, "_send_usage_reset_consume", _fake_consume)

    await service.apply_usage_reset(_ACCOUNT_ID)

    auth_manager.ensure_fresh.assert_awaited_once_with(stale_account, force=False)
    assert captured_kwargs["access_token"] == "fresh-access-token"


@pytest.mark.asyncio
async def test_apply_usage_reset_treats_already_redeemed_as_success(monkeypatch):
    account = _make_account(status=AccountStatus.RATE_LIMITED)
    service = _build_service(account=account, primary_pct=100.0, reset_count=1)

    async def _fake_consume(**kwargs):
        return ConsumeRateLimitResetResponse(code=ConsumeRateLimitResetCode.ALREADY_REDEEMED, windows_reset=0)

    monkeypatch.setattr(service, "_send_usage_reset_consume", _fake_consume)

    result = await service.apply_usage_reset(_ACCOUNT_ID)
    assert result is not None
    assert result.consume_code == "already_redeemed"


@pytest.mark.parametrize(
    "code",
    [ConsumeRateLimitResetCode.NO_CREDIT, ConsumeRateLimitResetCode.NOTHING_TO_RESET],
)
@pytest.mark.asyncio
async def test_apply_usage_reset_raises_when_upstream_rejects(code, monkeypatch):
    account = _make_account(status=AccountStatus.RATE_LIMITED)
    service = _build_service(account=account, primary_pct=100.0, reset_count=1)

    async def _fake_consume(**kwargs):
        return ConsumeRateLimitResetResponse(code=code, windows_reset=0)

    monkeypatch.setattr(service, "_send_usage_reset_consume", _fake_consume)

    with pytest.raises(AccountUsageResetRejectedError) as exc_info:
        await service.apply_usage_reset(_ACCOUNT_ID)

    assert exc_info.value.code == code.value


@pytest.mark.asyncio
async def test_apply_usage_reset_never_logs_access_token(monkeypatch, caplog):
    account = _make_account()
    service = _build_service(account=account, primary_pct=5.0, secondary_pct=5.0, reset_count=1)

    async def _fake_consume(**kwargs):
        return ConsumeRateLimitResetResponse(code=ConsumeRateLimitResetCode.RESET, windows_reset=1)

    monkeypatch.setattr(service, "_send_usage_reset_consume", _fake_consume)

    caplog.set_level("DEBUG")
    await service.apply_usage_reset(_ACCOUNT_ID)
    joined_log_output = "\n".join(record.getMessage() for record in caplog.records)
    assert _RESET_TOKEN_PLAINTEXT not in joined_log_output