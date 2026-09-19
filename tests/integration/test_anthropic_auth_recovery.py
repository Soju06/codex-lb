from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

import app.modules.proxy.anthropic_service as proxy
from app.core.auth.refresh import RefreshError
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.dependencies import _proxy_repo_context
from tests.integration.test_anthropic_proxy import (
    ANTHROPIC_JSON_BYTES,
    _FakeResponse,
    _FakeResponseContext,
    _insert_account,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("upstream_status", [401, 403])
async def test_message_auth_error_does_not_disable_account(async_client, monkeypatch, upstream_status):
    await _insert_account(
        account_id="auth-recovery", provider="anthropic", access_token="old-access", email="test@example.com"
    )
    forced = []
    seen = []

    async def ensure_fresh(self, account, *, force=False):
        if force:
            forced.append(account.id)
            async with SessionLocal() as session:
                stored = await session.get(Account, account.id)
                stored.access_token_encrypted = TokenEncryptor().encrypt("new-access")
                await session.commit()
            account.access_token_encrypted = TokenEncryptor().encrypt("new-access")
        return account

    def open_response(self, session, *, provider_name, headers, json_body):
        seen.append(headers["Authorization"])
        if len(seen) == 1 or upstream_status == 403:
            return _FakeResponseContext(_FakeResponse(upstream_status, b'{"error":{"message":"rejected"}}'))
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_JSON_BYTES))

    monkeypatch.setattr(proxy.AuthManager, "ensure_fresh", ensure_fresh)
    monkeypatch.setattr(proxy.AnthropicProxyService, "_open_upstream_response", open_response)
    response = await async_client.post(
        "/v1/messages",
        json={"model": "claude-sonnet-4-6", "max_tokens": 16, "messages": [{"role": "user", "content": "hi"}]},
    )
    async with SessionLocal() as session:
        stored = await session.get(Account, "auth-recovery")
        assert stored.status == AccountStatus.ACTIVE
    if upstream_status == 401:
        assert response.status_code == 200
        assert forced == ["auth-recovery"]
        assert seen == ["Bearer old-access", "Bearer new-access"]
    else:
        assert response.status_code >= 400
        assert forced == []
        assert seen == ["Bearer old-access"]


@pytest.mark.asyncio
async def test_stale_refresh_failure_does_not_overwrite_reauthorization(db_setup, monkeypatch):
    await _insert_account(
        account_id="reauthorized", provider="anthropic", access_token="old-access", email="test@example.com"
    )
    async with SessionLocal() as session:
        stale = (await session.execute(select(Account))).scalar_one()

    async def ensure_fresh(self, account, *, force=False):
        async with SessionLocal() as session:
            stored = await session.get(Account, account.id)
            stored.access_token_encrypted = TokenEncryptor().encrypt("reauthorized-access")
            stored.refresh_token_encrypted = TokenEncryptor().encrypt("reauthorized-refresh")
            await session.commit()
        raise RefreshError("invalid_grant", "old refresh failed", True)

    monkeypatch.setattr(proxy.AuthManager, "ensure_fresh", ensure_fresh)
    service = proxy.AnthropicProxyService(_proxy_repo_context)
    with pytest.raises(proxy.AnthropicProxyError):
        await service._fresh_access_token(stale)
    async with SessionLocal() as session:
        stored = await session.get(Account, stale.id)
        assert stored.status == AccountStatus.ACTIVE
        assert TokenEncryptor().decrypt(stored.refresh_token_encrypted) == "reauthorized-refresh"


@pytest.mark.asyncio
async def test_401_with_already_rotated_access_token_does_not_refresh_again(db_setup, monkeypatch):
    await _insert_account(
        account_id="rotated", provider="anthropic", access_token="new-access", email="test@example.com"
    )
    async with SessionLocal() as session:
        account = await session.get(Account, "rotated")
    ensure_fresh = AsyncMock(return_value=account)
    monkeypatch.setattr(proxy.AuthManager, "ensure_fresh", ensure_fresh)
    service = proxy.AnthropicProxyService(_proxy_repo_context)
    assert await service._fresh_access_token(account, rejected_access_token="old-access") == "new-access"
    assert ensure_fresh.call_args.kwargs == {"force": False}


@pytest.mark.asyncio
async def test_repeated_401_refreshes_once_then_fails_over(async_client, monkeypatch):
    await _insert_account(
        account_id="rejected", provider="anthropic", access_token="rejected-access", email="test@example.com"
    )
    forced = []
    calls = []

    async def ensure_fresh(self, account, *, force=False):
        if force:
            forced.append(account.id)
        return account

    def open_response(self, session, *, provider_name, headers, json_body):
        calls.append(headers["Authorization"])
        return _FakeResponseContext(_FakeResponse(401, b'{"error":{"message":"rejected"}}'))

    monkeypatch.setattr(proxy.AuthManager, "ensure_fresh", ensure_fresh)
    monkeypatch.setattr(proxy.AnthropicProxyService, "_open_upstream_response", open_response)
    response = await async_client.post(
        "/v1/messages",
        json={"model": "claude-sonnet-4-6", "max_tokens": 16, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code >= 400
    assert forced == ["rejected"]
    assert calls == ["Bearer rejected-access", "Bearer rejected-access"]
    async with SessionLocal() as session:
        assert (await session.get(Account, "rejected")).status == AccountStatus.ACTIVE
