from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_health_endpoint_ok(async_client):
    response = await async_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_api_validation_error_returns_dashboard_payload(async_client):
    response = await async_client.get("/api/usage/history?hours=0")
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "validation_error"
    assert payload["error"]["message"] == "Invalid request payload"


@pytest.mark.asyncio
async def test_api_not_found_returns_dashboard_payload(async_client):
    response = await async_client.get("/api/does-not-exist")
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["code"] == "http_404"
    assert payload["error"]["message"] == "Not Found"


@pytest.mark.asyncio
async def test_spa_route_path_returns_index_html(async_client):
    response = await async_client.get("/dashboard/settings")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


@pytest.mark.asyncio
async def test_missing_static_asset_returns_not_found(async_client):
    response = await async_client.get("/assets/missing.js")
    assert response.status_code == 404
    assert response.json()["detail"] == "Not Found"
