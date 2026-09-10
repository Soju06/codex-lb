"""Desktop reset settings retain the public dashboard auth and CSRF boundaries."""

from __future__ import annotations

import bcrypt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.core.auth.dashboard_users_cache import get_dashboard_users_cache
from app.core.middleware.dashboard_csrf import CROSS_SITE_REQUEST_REJECTED_CODE
from app.db.models import DashboardSettings, DashboardUser
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration
SAME_ORIGIN = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}


async def _bootstrap(client):
    response = await client.post("/api/dashboard-auth/password/setup", json={"password": "password123"})
    assert response.status_code == 200, response.text


async def _stored_enabled():
    async with SessionLocal() as session:
        return (await session.execute(select(DashboardSettings))).scalar_one().desktop_reset_pool_enabled


async def test_user_session_survives_same_origin_desktop_reset_setting_update(async_client):
    await _bootstrap(async_client)
    before = (await async_client.get("/api/dashboard-auth/me")).json()
    assert before["id"] and before["username"] == "admin"
    original = await _stored_enabled()
    response = await async_client.put(
        "/api/settings", json={"desktopResetPoolEnabled": not original}, headers=SAME_ORIGIN
    )
    assert response.status_code == 200, response.text
    assert response.json()["desktopResetPoolEnabled"] is not original
    assert await _stored_enabled() is not original
    after = await async_client.get("/api/dashboard-auth/me")
    assert after.status_code == 200, after.text
    assert after.json() == before


@pytest.mark.parametrize("headers", [{"Sec-Fetch-Site": "cross-site"}, {"Origin": "https://evil.example"}])
async def test_cross_site_desktop_reset_setting_does_not_mutate_authenticated_state(async_client, headers):
    await _bootstrap(async_client)
    original = await _stored_enabled()
    response = await async_client.put("/api/settings", json={"desktopResetPoolEnabled": not original}, headers=headers)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == CROSS_SITE_REQUEST_REJECTED_CODE
    assert await _stored_enabled() is original


@pytest.mark.parametrize("identity", ["guest", "viewer"])
async def test_read_only_identity_cannot_change_desktop_reset_setting(async_client, app_instance, identity):
    await _bootstrap(async_client)
    if identity == "guest":
        response = await async_client.put("/api/settings", json={"guestAccessEnabled": True}, headers=SAME_ORIGIN)
        assert response.status_code == 200, response.text
    else:
        async with SessionLocal() as session:
            session.add(
                DashboardUser(
                    id="desktop-reset-viewer",
                    username="desktop-reset-viewer",
                    role_id=PRESET_ROLE_IDS[PresetRoleSlug.VIEWER],
                    status="active",
                    password_hash=bcrypt.hashpw(b"viewer-password", bcrypt.gensalt(4)).decode(),
                )
            )
            await session.commit()
        await get_dashboard_users_cache().invalidate()
    original = await _stored_enabled()
    async with AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://testserver") as client:
        if identity == "guest":
            login = await client.post("/api/dashboard-auth/guest/login", json={})
        else:
            login = await client.post(
                "/api/dashboard-auth/password/login",
                json={"username": "desktop-reset-viewer", "password": "viewer-password"},
            )
        assert login.status_code == 200, login.text
        response = await client.put(
            "/api/settings", json={"desktopResetPoolEnabled": not original}, headers=SAME_ORIGIN
        )
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "read_only_access"
    assert await _stored_enabled() is original
