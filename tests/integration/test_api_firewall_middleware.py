from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse

from app.db.session import get_background_session
from app.modules.firewall.repository import FirewallRepository

pytestmark = pytest.mark.integration


async def _add_firewall_ip(ip_address: str) -> None:
    async with get_background_session() as session:
        await FirewallRepository(session).add(ip_address)
        await session.commit()


def test_protected_websocket_route_denies_unlisted_raw_peer_after_projection(
    app_instance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the real app stack projects an allowlisted forwarded client over an unlisted raw peer.
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    with TestClient(app_instance, client=("127.0.0.1", 50001)) as client:
        assert client.portal is not None
        client.portal.call(_add_firewall_ip, "203.0.113.9")

        # When: the protected WebSocket endpoint handles the projected handshake.
        with pytest.raises(WebSocketDenialResponse) as denial:
            with client.websocket_connect(
                "/v1/responses",
                headers={"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https"},
            ):
                pytest.fail("the unlisted raw peer must not complete the WebSocket handshake")

    # Then: the route returns the real firewall denial before authentication or upstream work.
    assert denial.value.status_code == 403
    assert denial.value.json()["error"]["code"] == "ip_forbidden"


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
