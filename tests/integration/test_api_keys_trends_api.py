from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_trends_returns_404_for_missing_key(async_client):
    response = await async_client.get("/api/api-keys/nonexistent/trends")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_trends_returns_empty_for_key_without_usage(async_client):
    create = await async_client.post(
        "/api/api-keys/",
        json={"name": "empty-key"},
    )
    assert create.status_code == 200
    key_id = create.json()["id"]

    response = await async_client.get(f"/api/api-keys/{key_id}/trends")
    assert response.status_code == 200
    body = response.json()
    assert body["keyId"] == key_id
    assert isinstance(body["cost"], list)
    assert isinstance(body["tokens"], list)


@pytest.mark.asyncio
async def test_usage_7d_returns_404_for_missing_key(async_client):
    response = await async_client.get("/api/api-keys/nonexistent/usage-7d")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_usage_7d_returns_zero_for_key_without_usage(async_client):
    create = await async_client.post(
        "/api/api-keys/",
        json={"name": "fresh-key"},
    )
    assert create.status_code == 200
    key_id = create.json()["id"]

    response = await async_client.get(f"/api/api-keys/{key_id}/usage-7d")
    assert response.status_code == 200
    body = response.json()
    assert body["keyId"] == key_id
    assert body["totalTokens"] == 0
    assert body["totalCostUsd"] == 0.0
    assert body["totalRequests"] == 0
    assert body["cachedInputTokens"] == 0


@pytest.mark.asyncio
async def test_trends_and_usage_7d_after_toggle_is_active(async_client):
    create = await async_client.post(
        "/api/api-keys/",
        json={"name": "toggle-key"},
    )
    assert create.status_code == 200
    key_id = create.json()["id"]
    assert create.json()["isActive"] is True

    patch = await async_client.patch(
        f"/api/api-keys/{key_id}",
        json={"isActive": False},
    )
    assert patch.status_code == 200
    assert patch.json()["isActive"] is False

    trends = await async_client.get(f"/api/api-keys/{key_id}/trends")
    assert trends.status_code == 200

    usage = await async_client.get(f"/api/api-keys/{key_id}/usage-7d")
    assert usage.status_code == 200
