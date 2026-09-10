from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import timedelta

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.modules.proxy.api as proxy_api
import app.modules.proxy.service as proxy_service
from app.core.clients.proxy_websocket import UpstreamWebSocketMessage
from app.core.config.dashboard_overrides import with_dashboard_overrides
from app.core.crypto import TokenEncryptor
from app.core.usage.live_hub import register_live_usage_publisher
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, UsageHistory
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy.load_balancer import AccountSelection

pytestmark = pytest.mark.integration


@dataclass
class QuotaPool:
    account: Account
    key: str
    reset_at: int


@pytest_asyncio.fixture
async def quota_pool(async_client):
    now = utcnow()
    reset_at = int((now + timedelta(days=3)).timestamp())
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        accounts = []
        for index, used in enumerate((97, 36)):
            account = Account(
                id=f"quota-pool-{index}",
                chatgpt_account_id=f"upstream-quota-{index}",
                email=f"quota-{index}@example.com",
                plan_type="pro",
                access_token_encrypted=encryptor.encrypt("access"),
                refresh_token_encrypted=encryptor.encrypt("refresh"),
                id_token_encrypted=encryptor.encrypt("id"),
                last_refresh=now,
                status=AccountStatus.ACTIVE,
            )
            session.add(account)
            accounts.append(account)
            session.add(
                UsageHistory(
                    account_id=account.id,
                    recorded_at=now,
                    window="primary",
                    used_percent=used,
                    window_minutes=10080,
                    reset_at=reset_at + index * 3600,
                    credits_has=False,
                    credits_unlimited=False,
                    credits_balance=0,
                )
            )
        await session.commit()
        for account in accounts:
            session.expunge(account)
    response = await async_client.post("/api/api-keys", json={"name": "pool-quota-test"})
    assert response.status_code == 200, response.text
    return QuotaPool(accounts[0], response.json()["key"], reset_at)


def _upstream_events():
    quota = {
        "type": "codex.rate_limits",
        "plan_type": "pro",
        "account_id": "must-not-leak",
        "rate_limits": {"primary": {"used_percent": 97, "window_minutes": 10080, "reset_at": 1900000000}},
        "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
    }
    return [
        quota,
        {**quota, "metered_limit_name": "codex_other"},
        {"type": "response.created", "response": {"id": "resp_quota", "status": "in_progress"}},
        {"type": "response.output_text.delta", "response_id": "resp_quota", "delta": "OK"},
        {
            "type": "response.completed",
            "response": {
                "id": "resp_quota",
                "status": "completed",
                "output": [],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
        },
    ]


class QuotaUpstream:
    def __init__(self):
        self.messages = asyncio.Queue()
        self.closed = False

    async def send_text(self, text):
        for event in _upstream_events():
            self.messages.put_nowait(UpstreamWebSocketMessage(kind="text", text=json.dumps(event)))

    async def receive(self):
        return await self.messages.get()

    async def close(self):
        self.closed = True


def _assert_downstream(events, *, hidden, reset_at):
    quotas = [event for event in events if event["type"] == "codex.rate_limits"]
    if hidden:
        assert quotas == []
    else:
        assert len(quotas) == 1
        assert quotas[0]["rate_limits"] == {
            "primary": None,
            "secondary": {"used_percent": 66.5, "window_minutes": 10080, "reset_at": reset_at},
        }
        assert "plan_type" not in quotas[0]
        assert "account_id" not in quotas[0]
    assert [event["type"] for event in events if event["type"] != "codex.rate_limits"] == [
        "response.created",
        "response.output_text.delta",
        "response.completed",
    ]
    assert next(event for event in events if event["type"] == "response.output_text.delta")["delta"] == "OK"


@pytest.mark.parametrize("transport", ["http", "bridge"])
@pytest.mark.parametrize("hidden", [False, True])
@pytest.mark.parametrize("path", ["/backend-api/codex/responses", "/backend-api/codex/responses/"])
async def test_native_sse_projects_pool_and_honors_privacy(
    async_client,
    app_instance,
    monkeypatch,
    quota_pool,
    transport,
    hidden,
    path,
):
    settings = await async_client.put(
        "/api/settings",
        json={
            "hideUpstreamQuotaFromApiKeys": hidden,
            "apiKeyAuthEnabled": True,
            "httpDownstreamTransportPolicy": "always_websocket" if transport == "bridge" else "always_http",
        },
    )
    assert settings.status_code == 200, settings.text
    app_settings = proxy_api.get_settings().model_copy(
        update={"http_responses_session_bridge_enabled": transport == "bridge"}
    )
    monkeypatch.setattr(proxy_api, "get_settings", lambda: with_dashboard_overrides(app_settings))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: with_dashboard_overrides(app_settings))
    upstream = QuotaUpstream()
    ingested = []
    register_live_usage_publisher(lambda snapshot, **identity: ingested.append((snapshot, identity)))

    async def select_account(self, *args, **kwargs):
        return AccountSelection(account=quota_pool.account, error_message=None, error_code=None)

    async def fresh(self, account, **kwargs):
        return account

    async def connect(*args, **kwargs):
        assert transport == "bridge"
        return upstream

    async def stream(*args, **kwargs):
        assert transport == "http"
        for event in _upstream_events():
            yield "data: " + json.dumps(event) + "\n\n"

    monkeypatch.setattr(proxy_service.ProxyService, "_select_account_with_budget", select_account)
    monkeypatch.setattr(proxy_service.ProxyService, "_ensure_fresh_with_budget", fresh)
    monkeypatch.setattr(proxy_service, "connect_responses_websocket", connect)
    monkeypatch.setattr(proxy_service, "core_stream_responses", stream)
    service = get_proxy_service_for_app(app_instance)
    try:
        response = await async_client.post(
            path,
            headers={
                "Authorization": f"Bearer {quota_pool.key}",
                "user-agent": "codex-cli/0.154.0",
            },
            json={"model": "gpt-5.6-sol", "instructions": "", "input": "hi", "stream": True},
        )
        assert response.status_code == 200, response.text
        events = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"
        ]
        events = [event for event in events if event["type"] != "codex.keepalive"]
        _assert_downstream(events, hidden=hidden, reset_at=quota_pool.reset_at)
        if hidden:
            assert not any(name.startswith("x-codex-secondary-") for name in response.headers)
        else:
            assert float(response.headers["x-codex-secondary-used-percent"]) == 66.5
        if transport == "bridge":
            assert any(
                snapshot.primary.used_percent == 97 and identity["account_id"] == quota_pool.account.id
                for snapshot, identity in ingested
            )
        async with SessionLocal() as session:
            usage = (
                (await session.execute(select(UsageHistory).where(UsageHistory.account_id == quota_pool.account.id)))
                .scalars()
                .all()
            )
            assert all(row.used_percent == 97 for row in usage)
    finally:
        register_live_usage_publisher(None)
        for session in list(service._http_bridge_sessions.values()):
            await service._close_http_bridge_session(session)


@pytest.mark.parametrize("path", ["/backend-api/codex/responses", "/v1/responses"])
@pytest.mark.parametrize("mode", ["visible", "hidden", "unavailable"])
async def test_websocket_projects_pool_without_interrupting_responses(
    async_client,
    app_instance,
    monkeypatch,
    quota_pool,
    path,
    mode,
):
    settings = await async_client.put(
        "/api/settings", json={"hideUpstreamQuotaFromApiKeys": mode == "hidden", "apiKeyAuthEnabled": True}
    )
    assert settings.status_code == 200, settings.text
    upstream = QuotaUpstream()

    async def connect(self, *args, **kwargs):
        return quota_pool.account, upstream

    async def unavailable(self):
        raise RuntimeError("quota cache unavailable")

    monkeypatch.setattr(proxy_service.ProxyService, "_connect_proxy_websocket", connect)
    if mode == "unavailable":
        monkeypatch.setattr(proxy_service.ProxyService, "rate_limit_headers", unavailable)

    def run_client():
        with TestClient(app_instance) as client:
            with client.websocket_connect(path, headers={"Authorization": f"Bearer {quota_pool.key}"}) as socket:
                socket.send_json({"type": "response.create", "model": "gpt-5.6-sol", "input": "hi"})
                events = []
                while not events or events[-1]["type"] != "response.completed":
                    events.append(socket.receive_json())
                return events

    events = await asyncio.to_thread(run_client)
    _assert_downstream(events, hidden=mode != "visible", reset_at=quota_pool.reset_at)
