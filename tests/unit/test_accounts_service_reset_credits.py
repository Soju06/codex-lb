from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.clients import rate_limit_resets
from app.core.clients.rate_limit_resets import ConsumeResetCreditPayload, ResetCreditDetails, ResetCreditsPayload
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus
from app.modules.accounts import reset_credit_cache
from app.modules.accounts import service as service_module
from app.modules.accounts.service import AccountResetCreditsUnavailableError, AccountsService

pytestmark = pytest.mark.unit


_ACCOUNT_ID = "acc_reset_test"
_CHATGPT_ACCOUNT_ID = "chatgpt-reset-acc-1"


@pytest.fixture(autouse=True)
def _reset_credit_count_cache():
    reset_credit_cache.reset()
    yield
    reset_credit_cache.reset()


@pytest.fixture(autouse=True)
def _recovery_dependencies(monkeypatch):
    monkeypatch.setattr(service_module, "refresh_standard_capacity", AsyncMock(return_value=True))


def _credits_payload(*statuses: str) -> ResetCreditsPayload:
    return ResetCreditsPayload(
        credits=[
            ResetCreditDetails(
                id=f"credit-{index}",
                reset_type="weekly",
                status=status,
                granted_at="2026-07-01T00:00:00Z",
                expires_at="2026-07-31T00:00:00Z",
            )
            for index, status in enumerate(statuses, start=1)
        ],
        available_count=sum(status == "available" for status in statuses),
    )


def _make_account(
    status: AccountStatus = AccountStatus.ACTIVE,
    provider: str = "openai",
) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=_ACCOUNT_ID,
        chatgpt_account_id=_CHATGPT_ACCOUNT_ID,
        email="reset@example.com",
        plan_type="pro",
        provider=provider,
        access_token_encrypted=encryptor.encrypt("test-access-token-not-a-real-secret"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=datetime(2026, 7, 17),
        status=status,
        deactivation_reason=None,
    )


def _build_service(
    account: Account | None,
    *,
    primary_pct: float | None = None,
    secondary_pct: float | None = None,
) -> AccountsService:
    repo = AsyncMock()
    repo.get_by_id.return_value = account

    usage_repo = AsyncMock()
    primary_entry = SimpleNamespace(used_percent=primary_pct) if primary_pct is not None else None
    secondary_entry = SimpleNamespace(used_percent=secondary_pct) if secondary_pct is not None else None

    async def _latest_entry_for_account(requested_account_id: str, *, window: str) -> Any:
        if requested_account_id != _ACCOUNT_ID:
            return None
        return primary_entry if window == "primary" else secondary_entry

    usage_repo.latest_entry_for_account.side_effect = _latest_entry_for_account

    service = AccountsService(repo=repo, usage_repo=usage_repo)
    usage_updater = AsyncMock()
    usage_updater.force_refresh = AsyncMock(return_value=True)
    service._usage_updater = usage_updater
    service._reset_attempts = AsyncMock()
    service._reset_attempts.active.return_value = None
    service._reset_attempts.create.side_effect = lambda account_id, credit_id, trigger: SimpleNamespace(
        id="logical-request-id",
        account_id=account_id,
        credit_id=credit_id,
        trigger=trigger,
        state="pending",
    )
    return service


@pytest.mark.asyncio
async def test_list_reset_credits_returns_none_for_missing_account():
    service = _build_service(account=None)
    assert await service.list_rate_limit_reset_credits("missing") is None


@pytest.mark.asyncio
async def test_list_reset_credits_rejects_non_openai_provider():
    service = _build_service(account=_make_account(provider="anthropic"))
    with pytest.raises(AccountResetCreditsUnavailableError):
        await service.list_rate_limit_reset_credits(_ACCOUNT_ID)


@pytest.mark.asyncio
async def test_list_reset_credits_rejects_paused_account():
    service = _build_service(account=_make_account(status=AccountStatus.PAUSED))
    with pytest.raises(AccountResetCreditsUnavailableError):
        await service.list_rate_limit_reset_credits(_ACCOUNT_ID)


@pytest.mark.asyncio
async def test_reset_credit_rejects_unusable_subscription():
    account = _make_account()
    account.subscription_status = "canceled"
    service = _build_service(account=account)
    with pytest.raises(AccountResetCreditsUnavailableError, match="subscription"):
        await service.redeem_rate_limit_reset_credit(_ACCOUNT_ID)
    service._reset_attempts.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_reset_credits_maps_upstream_payload(monkeypatch):
    service = _build_service(account=_make_account())

    async def _fake_fetch(**kwargs):
        assert kwargs["chatgpt_account_id"] == _CHATGPT_ACCOUNT_ID
        return ResetCreditsPayload(
            credits=[
                ResetCreditDetails(
                    id="credit-1",
                    reset_type="weekly",
                    status="available",
                    granted_at="2026-07-01T00:00:00Z",
                    expires_at="2026-07-31T00:00:00Z",
                    title="Saved reset",
                )
            ],
            available_count=1,
        )

    monkeypatch.setattr(rate_limit_resets, "fetch_reset_credits", _fake_fetch)

    result = await service.list_rate_limit_reset_credits(_ACCOUNT_ID)
    assert result is not None
    assert result.available_count == 1
    assert result.credits[0].id == "credit-1"
    assert result.credits[0].status == "available"
    assert reset_credit_cache.get_count(_ACCOUNT_ID) == 1


@pytest.mark.asyncio
async def test_redeem_reset_credit_success_refreshes_usage_and_credit_count(monkeypatch):
    service = _build_service(account=_make_account(), primary_pct=100.0, secondary_pct=100.0)
    captured: dict[str, Any] = {}

    async def _fake_consume(**kwargs):
        captured.update(kwargs)
        return ConsumeResetCreditPayload(code="reset", windows_reset=2)

    async def _fake_fetch(**kwargs):  # noqa: ARG001
        return _credits_payload("available", "redeemed")

    monkeypatch.setattr(rate_limit_resets, "consume_reset_credit", _fake_consume)
    monkeypatch.setattr(rate_limit_resets, "fetch_reset_credits", _fake_fetch)

    result = await service.redeem_rate_limit_reset_credit(_ACCOUNT_ID, credit_id="credit-1")
    assert result is not None
    assert result.status == "redeemed"
    assert result.code == "reset"
    assert result.windows_reset == 2
    assert result.primary_used_percent_before == 100.0
    assert captured["credit_id"] == "credit-1"
    # A fresh idempotency key must be generated per redemption attempt.
    assert isinstance(captured["redeem_request_id"], str) and captured["redeem_request_id"]
    service._usage_updater.force_refresh.assert_awaited_once()
    assert reset_credit_cache.get_count(_ACCOUNT_ID) == 1


@pytest.mark.asyncio
async def test_redeem_reset_credit_refetch_failure_clears_cached_count(monkeypatch):
    service = _build_service(account=_make_account())
    reset_credit_cache.record_count(_ACCOUNT_ID, 3)

    async def _fake_consume(**kwargs):  # noqa: ARG001
        return ConsumeResetCreditPayload(code="reset", windows_reset=1)

    fetches = 0

    async def _fake_fetch(**kwargs):  # noqa: ARG001
        nonlocal fetches
        fetches += 1
        if fetches <= 2:
            return _credits_payload("available")
        raise RuntimeError("listing unavailable")

    monkeypatch.setattr(rate_limit_resets, "consume_reset_credit", _fake_consume)
    monkeypatch.setattr(rate_limit_resets, "fetch_reset_credits", _fake_fetch)

    result = await service.redeem_rate_limit_reset_credit(_ACCOUNT_ID)

    assert result is not None
    assert result.status == "redeemed"
    assert reset_credit_cache.get_count(_ACCOUNT_ID) is None


@pytest.mark.asyncio
async def test_redeem_reset_credit_no_credit_skips_refresh(monkeypatch):
    service = _build_service(account=_make_account())

    async def _fake_consume(**kwargs):
        return ConsumeResetCreditPayload(code="no_credit", windows_reset=0)

    monkeypatch.setattr(rate_limit_resets, "consume_reset_credit", _fake_consume)
    monkeypatch.setattr(rate_limit_resets, "fetch_reset_credits", AsyncMock(return_value=_credits_payload()))

    result = await service.redeem_rate_limit_reset_credit(_ACCOUNT_ID)
    assert result is not None
    assert result.status == "not_redeemed"
    assert result.code == "no_credit"
    assert result.windows_reset == 0
    service._usage_updater.force_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_redeem_reset_credit_already_redeemed_is_idempotent_success(monkeypatch):
    service = _build_service(account=_make_account())

    async def _fake_consume(**kwargs):
        return ConsumeResetCreditPayload(code="already_redeemed", windows_reset=0)

    async def _fake_fetch(**kwargs):  # noqa: ARG001
        return _credits_payload("available")

    monkeypatch.setattr(rate_limit_resets, "consume_reset_credit", _fake_consume)
    monkeypatch.setattr(rate_limit_resets, "fetch_reset_credits", _fake_fetch)

    result = await service.redeem_rate_limit_reset_credit(_ACCOUNT_ID)
    assert result is not None
    assert result.status == "redeemed"
    service._usage_updater.force_refresh.assert_awaited_once()
