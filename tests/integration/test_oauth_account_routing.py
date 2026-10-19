from __future__ import annotations

import asyncio
import base64
import contextlib
import json
from datetime import datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest
from aiohttp import web
from sqlalchemy import select

from app.core.clients.oauth import DeviceCode, OAuthTokens
from app.core.crypto import TokenEncryptor
from app.db.models import (
    Account,
    AccountProxyBinding,
    AccountStatus,
    DashboardSettings,
    ProxyEndpoint,
    ProxyPool,
    ProxyPoolMember,
)
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.oauth import service as oauth

pytestmark = pytest.mark.integration


async def _drain_store() -> None:
    store = oauth._OAUTH_STORE
    async with store.lock:
        tasks = [flow.poll_task for flow in store._flows.values() if flow.poll_task and not flow.poll_task.done()]
        if store._callback_server_stop_task and not store._callback_server_stop_task.done():
            tasks.append(store._callback_server_stop_task)
    await store.reset()
    for task in tasks:
        task.cancel()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await task


@pytest.fixture(autouse=True)
async def _isolate_oauth(db_setup, monkeypatch):
    del db_setup
    await _drain_store()
    monkeypatch.setattr(oauth.OAuthCallbackServer, "start", AsyncMock())
    monkeypatch.setattr(oauth.OAuthCallbackServer, "stop", AsyncMock())
    yield
    await _drain_store()


async def _seed_accounts(*, pool_active: bool = True) -> None:
    encrypted = TokenEncryptor().encrypt("test-token")
    async with SessionLocal() as session:
        for account_id in ("proxied", "direct"):
            session.add(
                Account(
                    id=account_id,
                    email=f"{account_id}@example.test",
                    plan_type="business",
                    chatgpt_account_id="same-business-workspace",
                    chatgpt_user_id=f"{account_id}-seat",
                    access_token_encrypted=encrypted,
                    refresh_token_encrypted=encrypted,
                    id_token_encrypted=encrypted,
                    last_refresh=datetime(2026, 1, 1),
                    status=AccountStatus.ACTIVE,
                )
            )
        pool = ProxyPool(id="proxied-pool", name="Proxied test pool", is_active=pool_active)
        endpoint = ProxyEndpoint(
            id="proxied-endpoint", name="Proxied test endpoint", scheme="http", host="proxy.example.test", port=8080
        )
        session.add_all(
            [
                pool,
                endpoint,
                ProxyPoolMember(id="member", pool=pool, endpoint=endpoint),
                AccountProxyBinding(id="binding", account_id="proxied", pool_id="proxied-pool"),
            ]
        )
        settings = await session.get(DashboardSettings, 1)
        if settings is None:
            settings = DashboardSettings(id=1)
            session.add(settings)
        settings.upstream_proxy_routing_enabled = False
        settings.upstream_proxy_default_pool_id = None
        await session.commit()


def _assert_route(account_id, kwargs):
    route = kwargs["route"]
    if account_id == "proxied":
        assert route is not None
        assert route.mode == "account_bound"
        assert route.pool_id == "proxied-pool"
        assert route.endpoint_id == "proxied-endpoint"
        assert not route.fallbacks
        assert kwargs["allow_direct_egress"] is False
    else:
        assert route is None
        assert kwargs["allow_direct_egress"] is True


@pytest.mark.parametrize("account_id", ["proxied", "direct"])
@pytest.mark.parametrize("callback", ["manual", "browser", "manual-rehydrated"])
async def test_targeted_browser_flow_uses_account_route(async_client, monkeypatch, account_id, callback):
    await _seed_accounts()
    exchange = AsyncMock(return_value=OAuthTokens(access_token="access", refresh_token="refresh", id_token="id"))
    persist = AsyncMock()
    monkeypatch.setattr(oauth, "exchange_authorization_code", exchange)
    monkeypatch.setattr(oauth.OauthService, "_persist_tokens", persist)
    start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser", "accountId": account_id})
    assert start.status_code == 200
    payload = start.json()
    state = parse_qs(urlparse(payload["authorizationUrl"]).query)["state"][0]
    if callback == "manual-rehydrated":
        await _drain_store()
    if callback.startswith("manual"):
        response = await async_client.post(
            "/api/oauth/manual-callback",
            json={"flowId": payload["flowId"], "callbackUrl": f"http://localhost:1455/?code=test-code&state={state}"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "success", response.json()
    else:
        service = oauth.OauthService(cast(AccountsRepository, SimpleNamespace()))
        request = cast(
            web.Request, SimpleNamespace(rel_url=SimpleNamespace(query={"code": "test-code", "state": state}))
        )
        response = await service._handle_callback(request)
        assert response.status == 200
        status = await async_client.get("/api/oauth/status", params={"flowId": payload["flowId"]})
        assert status.json()["status"] == "success", status.json()
    exchange.assert_awaited_once()
    assert exchange.await_args is not None
    _assert_route(account_id, exchange.await_args.kwargs)
    persist.assert_awaited_once()
    assert persist.await_args is not None
    assert persist.await_args.kwargs["intended_account_id"] == account_id


@pytest.mark.parametrize("account_id", ["proxied", "direct"])
async def test_targeted_device_flow_uses_same_account_route_at_both_stages(async_client, monkeypatch, account_id):
    await _seed_accounts()
    device = AsyncMock(
        return_value=DeviceCode(
            verification_url="https://auth.example.test/device",
            user_code="TEST-CODE",
            device_auth_id="test-device",
            interval_seconds=1,
            expires_in_seconds=30,
        )
    )
    exchange = AsyncMock(return_value=OAuthTokens(access_token="access", refresh_token="refresh", id_token="id"))
    persist = AsyncMock()
    monkeypatch.setattr(oauth, "request_device_code", device)
    monkeypatch.setattr(oauth, "exchange_device_token", exchange)
    monkeypatch.setattr(oauth.OauthService, "_persist_tokens", persist)
    start = await async_client.post("/api/oauth/start", json={"forceMethod": "device", "accountId": account_id})
    assert start.status_code == 200, start.json()
    for _ in range(40):
        status = await async_client.get("/api/oauth/status", params={"flowId": start.json()["flowId"]})
        if status.json()["status"] == "success":
            break
        await asyncio.sleep(0.025)
    assert status.json()["status"] == "success", status.json()
    device.assert_awaited_once()
    assert device.await_args is not None
    exchange.assert_awaited_once()
    assert exchange.await_args is not None
    _assert_route(account_id, device.await_args.kwargs)
    _assert_route(account_id, exchange.await_args.kwargs)
    persist.assert_awaited_once()
    assert persist.await_args is not None
    assert persist.await_args.kwargs["intended_account_id"] == account_id


async def test_targeted_inactive_pool_never_opens_device_connection(async_client, monkeypatch):
    await _seed_accounts(pool_active=False)
    device = AsyncMock()
    monkeypatch.setattr(oauth, "request_device_code", device)
    response = await async_client.post("/api/oauth/start", json={"forceMethod": "device", "accountId": "proxied"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "pool_unavailable", response.json()
    device.assert_not_awaited()


async def test_unknown_target_never_opens_device_connection(async_client, monkeypatch):
    await _seed_accounts()
    device = AsyncMock()
    monkeypatch.setattr(oauth, "request_device_code", device)
    response = await async_client.post("/api/oauth/start", json={"forceMethod": "device", "accountId": "missing"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "oauth_account_not_found", response.json()
    device.assert_not_awaited()


@pytest.mark.parametrize("returned_account", ["proxied", "direct"])
async def test_real_store_replacement_preserves_account_identity_and_other_credentials(async_client, returned_account):
    del async_client
    await _seed_accounts()
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        before = {
            row.id: (row.access_token_encrypted, row.refresh_token_encrypted, row.id_token_encrypted)
            for row in (await session.execute(select(Account))).scalars()
        }
        claims = {
            "email": f"{returned_account}@example.test",
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "same-business-workspace",
                "chatgpt_user_id": f"{returned_account}-seat",
                "chatgpt_plan_type": "business",
            },
        }
        body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
        tokens = OAuthTokens(access_token="new-access", refresh_token="new-refresh", id_token=f"header.{body}.sig")
        service = oauth.OauthService(AccountsRepository(session))
        if returned_account == "direct":
            with pytest.raises(oauth.ReauthSeatMismatchError):
                await service._persist_tokens(tokens, intended_account_id="proxied")
        else:
            await service._persist_tokens(tokens, intended_account_id="proxied")
    async with SessionLocal() as session:
        after = {
            row.id: (row.access_token_encrypted, row.refresh_token_encrypted, row.id_token_encrypted)
            for row in (await session.execute(select(Account))).scalars()
        }
    assert set(after) == {"proxied", "direct"}
    assert after["direct"] == before["direct"]
    if returned_account == "direct":
        assert after == before
    else:
        assert encryptor.decrypt(after["proxied"][0]) == "new-access"
        assert encryptor.decrypt(after["proxied"][1]) == "new-refresh"
