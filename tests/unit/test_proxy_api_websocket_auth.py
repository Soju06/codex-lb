from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import WebSocket
from fastapi.responses import JSONResponse
from starlette.requests import Request

import app.modules.proxy.api as proxy_api_module
from app.core.errors import openai_error
from app.core.exceptions import ProxyAuthError
from app.modules.api_keys.service import ApiKeyData

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_validate_proxy_websocket_request_returns_firewall_denial(monkeypatch):
    denial = JSONResponse(
        status_code=403,
        content=openai_error("ip_forbidden", "Access denied for client IP", error_type="access_error"),
    )

    async def fake_denial(_websocket):
        return denial

    async def fail_auth(_authorization):
        raise AssertionError("authorization validation must not run when firewall already denied the websocket")

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", fake_denial)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", fail_auth)

    api_key, response = await proxy_api_module._validate_proxy_websocket_request(
        cast(WebSocket, SimpleNamespace(headers={})),
    )

    assert api_key is None
    assert response is denial


@pytest.mark.asyncio
async def test_validate_proxy_websocket_request_maps_auth_error(monkeypatch):
    async def fake_denial(_websocket):
        return None

    async def fail_auth(_authorization):
        raise ProxyAuthError("Missing API key in Authorization header")

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", fake_denial)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", fail_auth)

    api_key, response = await proxy_api_module._validate_proxy_websocket_request(
        cast(WebSocket, SimpleNamespace(headers={"authorization": "Bearer invalid"})),
    )

    assert api_key is None
    assert response is not None
    assert response.status_code == 401
    payload = json.loads(cast(bytes, response.body).decode("utf-8"))
    assert payload["error"]["code"] == "invalid_api_key"
    assert payload["error"]["message"] == "Missing API key in Authorization header"


@pytest.mark.asyncio
async def test_validate_proxy_websocket_request_returns_validated_api_key(monkeypatch):
    async def fake_denial(_websocket):
        return None

    api_key = ApiKeyData(
        id="key_1",
        name="Test Key",
        key_prefix="sk-test",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=datetime(2026, 3, 10),
        last_used_at=None,
    )

    async def pass_auth(authorization: str | None):
        assert authorization == "Bearer valid-key"
        return api_key

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", fake_denial)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", pass_auth)

    resolved_api_key, response = await proxy_api_module._validate_proxy_websocket_request(
        cast(WebSocket, SimpleNamespace(headers={"authorization": "Bearer valid-key"})),
    )

    assert response is None
    assert resolved_api_key == api_key


@pytest.mark.asyncio
async def test_validate_internal_bridge_api_key_allows_auth_disabled_remote_request(monkeypatch):
    async def pass_auth(authorization: str | None):
        assert authorization is None
        return None

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/internal/bridge/responses",
            "headers": [],
            "client": ("10.0.0.12", 12345),
        }
    )
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", pass_auth)

    api_key, response = await proxy_api_module._validate_internal_bridge_api_key(request)

    assert api_key is None
    assert response is None
