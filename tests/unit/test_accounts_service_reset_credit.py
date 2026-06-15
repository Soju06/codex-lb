from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.crypto import TokenEncryptor
from app.core.exceptions import DashboardConflictError
from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute
from app.db.models import Account, AccountStatus
from app.modules.accounts.service import AccountsService

pytestmark = pytest.mark.unit

_ACCOUNT_ID = "acc_reset_credit"
_CHATGPT_ACCOUNT_ID = "chatgpt-reset-credit"


def _make_account() -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=_ACCOUNT_ID,
        chatgpt_account_id=_CHATGPT_ACCOUNT_ID,
        email="reset@example.com",
        plan_type="pro",
        access_token_encrypted=encryptor.encrypt("reset-access-token"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=datetime(2026, 6, 16, 12, 0, 0),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


def _build_service(account: Account | None) -> tuple[AccountsService, AsyncMock]:
    repo = AsyncMock()
    repo.get_by_id.return_value = account
    repo.claim_nearest_expiry_available_credit_id.return_value = "credit_one"
    repo.session = object()
    return AccountsService(repo=repo), repo


@pytest.mark.asyncio
async def test_reset_account_credit_uses_resolved_route(monkeypatch) -> None:
    service, repo = _build_service(_make_account())
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
    )
    captured_kwargs: dict[str, object] = {}

    async def consume_stub(**kwargs: object) -> SimpleNamespace:
        captured_kwargs.update(kwargs)
        return SimpleNamespace(windows_reset=2)

    monkeypatch.setattr("app.modules.accounts.service.resolve_upstream_route", AsyncMock(return_value=route))
    monkeypatch.setattr(
        "app.core.clients.rate_limit_reset_credits.consume_rate_limit_reset_credit",
        consume_stub,
    )

    result = await service.reset_account_credit(_ACCOUNT_ID)

    assert result is not None
    assert result.credit_id == "credit_one"
    assert result.windows_reset == 2
    assert captured_kwargs["route"] is route
    assert captured_kwargs["allow_direct_egress"] is False
    repo.claim_nearest_expiry_available_credit_id.assert_awaited_once_with(_ACCOUNT_ID)
    repo.mark_credit_redeemed.assert_awaited_once_with(_ACCOUNT_ID, "credit_one")
    repo.release_claimed_credit.assert_not_called()


@pytest.mark.asyncio
async def test_reset_account_credit_releases_claim_when_consume_fails(monkeypatch) -> None:
    service, repo = _build_service(_make_account())

    async def consume_stub(**kwargs: object) -> SimpleNamespace:
        raise RuntimeError("boom")

    monkeypatch.setattr("app.modules.accounts.service.resolve_upstream_route", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "app.core.clients.rate_limit_reset_credits.consume_rate_limit_reset_credit",
        consume_stub,
    )

    with pytest.raises(RuntimeError, match="boom"):
        await service.reset_account_credit(_ACCOUNT_ID)

    repo.release_claimed_credit.assert_awaited_once_with(_ACCOUNT_ID, "credit_one")
    repo.mark_credit_redeemed.assert_not_called()


@pytest.mark.asyncio
async def test_reset_account_credit_fails_when_claim_finds_no_available_credit() -> None:
    service, repo = _build_service(_make_account())
    repo.claim_nearest_expiry_available_credit_id.return_value = None

    with pytest.raises(DashboardConflictError, match="No available rate-limit reset credits"):
        await service.reset_account_credit(_ACCOUNT_ID)

    repo.mark_credit_redeemed.assert_not_called()
    repo.release_claimed_credit.assert_not_called()
