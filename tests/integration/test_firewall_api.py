from __future__ import annotations

from urllib.parse import quote

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_firewall_api_crud_and_mode(async_client):
    initial = await async_client.get("/api/firewall/ips")
    assert initial.status_code == 200
    initial_payload = initial.json()
    assert initial_payload["mode"] == "allow_all"
    assert initial_payload["entries"] == []

    created = await async_client.post("/api/firewall/ips", json={"ipAddress": "127.0.0.1"})
    assert created.status_code == 200
    created_payload = created.json()
    assert created_payload["ipAddress"] == "127.0.0.1"
    assert isinstance(created_payload["createdAt"], str)

    listed = await async_client.get("/api/firewall/ips")
    assert listed.status_code == 200
    listed_payload = listed.json()
    assert listed_payload["mode"] == "allowlist_active"
    assert len(listed_payload["entries"]) == 1
    assert listed_payload["entries"][0]["ipAddress"] == "127.0.0.1"

    duplicate = await async_client.post("/api/firewall/ips", json={"ipAddress": "127.0.0.1"})
    assert duplicate.status_code == 409
    duplicate_payload = duplicate.json()
    assert duplicate_payload["error"]["code"] == "ip_exists"

    invalid = await async_client.post("/api/firewall/ips", json={"ipAddress": "not-an-ip"})
    assert invalid.status_code == 400
    invalid_payload = invalid.json()
    assert invalid_payload["error"]["code"] == "invalid_ip"

    deleted = await async_client.delete("/api/firewall/ips/127.0.0.1")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"

    missing = await async_client.delete("/api/firewall/ips/127.0.0.1")
    assert missing.status_code == 404
    missing_payload = missing.json()
    assert missing_payload["error"]["code"] == "ip_not_found"

    final = await async_client.get("/api/firewall/ips")
    assert final.status_code == 200
    final_payload = final.json()
    assert final_payload["mode"] == "allow_all"
    assert final_payload["entries"] == []


@pytest.mark.asyncio
async def test_firewall_api_ipv6_normalized(async_client):
    created = await async_client.post(
        "/api/firewall/ips",
        json={"ipAddress": "2001:0db8:0000:0000:0000:ff00:0042:8329"},
    )
    assert created.status_code == 200
    payload = created.json()
    assert payload["ipAddress"] == "2001:db8::ff00:42:8329"

    encoded = quote(payload["ipAddress"], safe="")
    deleted = await async_client.delete(f"/api/firewall/ips/{encoded}")
    assert deleted.status_code == 200
