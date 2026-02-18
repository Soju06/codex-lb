from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_firewall_middleware_allows_v1_when_allowlist_empty(async_client):
    response = await async_client.get("/v1/models")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_firewall_middleware_blocks_v1_when_ip_not_allowed(async_client):
    add_response = await async_client.post("/api/firewall/ips", json={"ipAddress": "10.20.30.40"})
    assert add_response.status_code == 200

    response = await async_client.get("/v1/models")
    assert response.status_code == 403
    payload = response.json()
    assert payload["error"]["code"] == "ip_forbidden"


@pytest.mark.asyncio
async def test_firewall_middleware_allows_v1_for_allowed_loopback_ip(async_client):
    add_response = await async_client.post("/api/firewall/ips", json={"ipAddress": "127.0.0.1"})
    assert add_response.status_code == 200

    response = await async_client.get("/v1/models")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_firewall_middleware_blocks_backend_api_when_ip_not_allowed(async_client):
    add_response = await async_client.post("/api/firewall/ips", json={"ipAddress": "203.0.113.7"})
    assert add_response.status_code == 200

    response = await async_client.get("/backend-api/codex/models")
    assert response.status_code == 403
    payload = response.json()
    assert payload["error"]["code"] == "ip_forbidden"


@pytest.mark.asyncio
async def test_firewall_middleware_does_not_restrict_dashboard_routes(async_client):
    add_response = await async_client.post("/api/firewall/ips", json={"ipAddress": "203.0.113.7"})
    assert add_response.status_code == 200

    settings_response = await async_client.get("/api/settings")
    assert settings_response.status_code == 200


@pytest.mark.asyncio
async def test_firewall_middleware_does_not_restrict_codex_usage_route(async_client):
    add_response = await async_client.post("/api/firewall/ips", json={"ipAddress": "203.0.113.7"})
    assert add_response.status_code == 200

    response = await async_client.get("/api/codex/usage")
    assert response.status_code == 401
