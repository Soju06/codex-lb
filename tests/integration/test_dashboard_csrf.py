"""Cross-site request rejection on the real application stack.

The CSRF middleware runs before routing and before any authentication
dependency, so a cross-site mutation is rejected even when no session exists
and even when the target route has no session gate at all (``/logout``).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.middleware.dashboard_csrf import CROSS_SITE_REQUEST_REJECTED_CODE

pytestmark = pytest.mark.integration

CROSS_SITE = {"Sec-Fetch-Site": "cross-site"}
SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}


def _assert_cross_site_rejected(response) -> None:
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == CROSS_SITE_REQUEST_REJECTED_CODE


async def test_cross_site_logout_is_rejected_before_authentication(async_client: AsyncClient) -> None:
    response = await async_client.post("/api/dashboard-auth/logout", headers=CROSS_SITE)
    _assert_cross_site_rejected(response)


async def test_same_origin_logout_reaches_the_handler(async_client: AsyncClient) -> None:
    response = await async_client.post("/api/dashboard-auth/logout", headers=SAME_ORIGIN)
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok"}


async def test_cross_site_settings_update_is_rejected_before_authentication(async_client: AsyncClient) -> None:
    response = await async_client.put("/api/settings", json={}, headers=CROSS_SITE)
    _assert_cross_site_rejected(response)


async def test_same_origin_settings_update_reaches_the_handler(async_client: AsyncClient) -> None:
    response = await async_client.put("/api/settings", json={}, headers=SAME_ORIGIN)
    assert response.status_code == 200, response.text
    assert "error" not in response.json()


async def test_foreign_origin_settings_update_is_rejected(async_client: AsyncClient) -> None:
    response = await async_client.put("/api/settings", json={}, headers={"Origin": "http://evil.example"})
    _assert_cross_site_rejected(response)


async def test_matching_origin_settings_update_reaches_the_handler(async_client: AsyncClient) -> None:
    response = await async_client.put("/api/settings", json={}, headers={"Origin": "http://testserver"})
    assert response.status_code == 200, response.text


async def test_cross_site_read_is_not_affected(async_client: AsyncClient) -> None:
    response = await async_client.get("/api/dashboard-auth/session", headers=CROSS_SITE)
    assert response.status_code == 200, response.text


async def test_cross_site_fleet_refresh_is_left_to_bearer_authentication(async_client: AsyncClient) -> None:
    response = await async_client.post("/api/fleet/refresh", headers=CROSS_SITE)
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] != CROSS_SITE_REQUEST_REJECTED_CODE


async def test_forwarded_https_scheme_is_used_for_origin_comparison(async_client: AsyncClient) -> None:
    """The default test client is the trusted loopback peer, so uvicorn's proxy-header
    middleware rewrites the scheme from ``X-Forwarded-Proto`` before the origin check."""

    response = await async_client.put(
        "/api/settings",
        json={},
        headers={"X-Forwarded-Proto": "https", "Origin": "https://testserver"},
    )
    assert response.status_code == 200, response.text
