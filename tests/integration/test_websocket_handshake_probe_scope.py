"""A rejected websocket handshake keeps the scope needed for operator recovery."""

import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.modules.proxy.api as proxy_api
import app.modules.proxy.service as proxy_module
from app.core.auth import generate_unique_account_id
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.modules.accounts.auth_manager import AuthManager
from app.modules.usage.updater import AccountRefreshResult, UsageUpdater
from tests.integration.test_accounts_verified_probe import upstream
from tests.integration.test_proxy_transient_retry import _make_auth_json

pytestmark = pytest.mark.integration


def test_completed_probe_recovers_matching_websocket_handshake_rejection(app_instance, monkeypatch):
    async def fresh(self, account, **kwargs):
        return account

    async def allow(*args, **kwargs):
        return None

    monkeypatch.setattr(AuthManager, "ensure_fresh", fresh)
    monkeypatch.setattr(proxy_api, "_websocket_firewall_denial_response", allow)
    monkeypatch.setattr(proxy_api, "validate_proxy_api_key_authorization", allow)
    monkeypatch.setattr(
        proxy_module.ProxyService,
        "_open_upstream_websocket",
        AsyncMock(side_effect=ProxyResponseError(429, openai_error("usage_limit_reached", "model exhausted"))),
    )
    monkeypatch.setattr(
        UsageUpdater,
        "force_refresh_result",
        AsyncMock(return_value=AccountRefreshResult(usage_written=False, fetch_succeeded=False)),
    )
    calls = upstream(monkeypatch)
    auth = _make_auth_json("handshake-scope", "handshake@example.invalid")
    account_id = generate_unique_account_id("handshake-scope", "handshake@example.invalid")
    with TestClient(app_instance, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        imported = client.post(
            "/api/accounts/import", files={"auth_json": ("auth.json", json.dumps(auth), "application/json")}
        )
        assert imported.status_code == 200
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_json(
                {
                    "type": "response.create",
                    "model": "gpt-5.1",
                    "service_tier": "priority",
                    "input": [],
                    "instructions": "",
                }
            )
            failure = websocket.receive_json()
        assert failure["type"] == "error"
        assert failure["error"]["code"] == "usage_limit_reached", failure
        probe = client.post(f"/api/accounts/{account_id}/probe")
        assert probe.status_code == 200
        assert probe.json()["probeCompleted"] is True
        assert probe.json()["holdRecovered"] is True
    assert calls[0][1]["model"] == "gpt-5.1"
    assert calls[0][1]["service_tier"] == "priority"
