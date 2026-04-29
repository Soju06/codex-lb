from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_peer_fallback_targets_api_crud(async_client):
    initial = await async_client.get("/api/peer-fallback-targets")
    assert initial.status_code == 200
    assert initial.json()["targets"] == []

    created = await async_client.post(
        "/api/peer-fallback-targets",
        json={"baseUrl": " http://127.0.0.1:2456/ ", "enabled": True},
    )
    assert created.status_code == 200
    created_payload = created.json()
    assert created_payload["baseUrl"] == "http://127.0.0.1:2456"
    assert created_payload["enabled"] is True
    assert isinstance(created_payload["id"], str)
    assert isinstance(created_payload["createdAt"], str)
    target_id = created_payload["id"]

    duplicate = await async_client.post(
        "/api/peer-fallback-targets",
        json={"baseUrl": "http://127.0.0.1:2456"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "peer_fallback_target_exists"

    invalid = await async_client.post(
        "/api/peer-fallback-targets",
        json={"baseUrl": "127.0.0.1:2456"},
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_peer_fallback_target"

    invalid_query = await async_client.post(
        "/api/peer-fallback-targets",
        json={"baseUrl": "https://peer.example?token=x"},
    )
    assert invalid_query.status_code == 400
    assert invalid_query.json()["error"]["code"] == "invalid_peer_fallback_target"

    for malformed_url in ("http://example.com:abc", "http://exa mple.com", "http://[::1"):
        invalid_malformed = await async_client.post(
            "/api/peer-fallback-targets",
            json={"baseUrl": malformed_url},
        )
        assert invalid_malformed.status_code == 400
        assert invalid_malformed.json()["error"]["code"] == "invalid_peer_fallback_target"

    updated = await async_client.patch(
        f"/api/peer-fallback-targets/{target_id}",
        json={"enabled": False, "baseUrl": "https://peer.example/path/"},
    )
    assert updated.status_code == 200
    updated_payload = updated.json()
    assert updated_payload["baseUrl"] == "https://peer.example/path"
    assert updated_payload["enabled"] is False

    listed = await async_client.get("/api/peer-fallback-targets")
    assert listed.status_code == 200
    listed_payload = listed.json()
    assert [target["id"] for target in listed_payload["targets"]] == [target_id]

    deleted = await async_client.delete(f"/api/peer-fallback-targets/{target_id}")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"

    missing = await async_client.delete(f"/api/peer-fallback-targets/{target_id}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "peer_fallback_target_not_found"
