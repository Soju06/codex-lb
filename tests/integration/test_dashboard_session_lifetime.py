from __future__ import annotations

from http.cookies import SimpleCookie

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import app.modules.dashboard_auth.service as auth_service
from app.modules.dashboard_auth.service import DASHBOARD_SESSION_COOKIE, get_dashboard_session_store

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("configured_ttl", "expected_ttl"),
    [
        (3600, 3600),
        (1_224_000, 1_224_000),
        (30 * 24 * 60 * 60, 30 * 24 * 60 * 60),
        (30 * 24 * 60 * 60 + 1, 12 * 60 * 60),
        (365 * 24 * 60 * 60, 12 * 60 * 60),
    ],
)
async def test_remote_admin_login_uses_configured_password_session_lifetime(
    async_client: AsyncClient,
    app_instance: FastAPI,
    configured_ttl: int,
    expected_ttl: int,
) -> None:
    setup = await async_client.post("/api/dashboard-auth/password/setup", json={"password": "password123"})
    assert setup.status_code == 200
    settings = await async_client.get("/api/settings")
    updated = await async_client.put(
        "/api/settings",
        json={**settings.json(), "dashboardSessionTtlSeconds": configured_ttl},
    )
    assert updated.status_code == 200

    async with AsyncClient(
        transport=ASGITransport(app=app_instance, client=("192.0.2.1", 50001)),
        base_url="https://dashboard.example",
        headers={"x-forwarded-for": "100.64.0.2", "x-forwarded-proto": "https"},
    ) as remote:
        login = await remote.post("/api/dashboard-auth/password/login", json={"password": "password123"})

        assert login.status_code == 200
        assert login.json()["user"]["role"]["slug"] == "admin"
        cookie = SimpleCookie(login.headers["set-cookie"])[DASHBOARD_SESSION_COOKIE]
        assert int(cookie["max-age"]) == expected_ttl
        assert cookie["httponly"] and cookie["secure"] and cookie["samesite"] == "lax"
        state = get_dashboard_session_store().get(cookie.value)
        assert state is not None
        assert state.expires_at - state.issued_at == expected_ttl
        assert (await remote.get("/api/settings")).status_code == 200


async def test_remote_admin_session_survives_twelve_hours_but_keeps_absolute_expiry(
    async_client: AsyncClient,
    app_instance: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issued_at = 1_800_000_000
    configured_ttl = 1_224_000
    monkeypatch.setattr(auth_service, "time", lambda: issued_at)
    setup = await async_client.post("/api/dashboard-auth/password/setup", json={"password": "password123"})
    assert setup.status_code == 200
    settings = await async_client.get("/api/settings")
    updated = await async_client.put(
        "/api/settings",
        json={**settings.json(), "dashboardSessionTtlSeconds": configured_ttl},
    )
    assert updated.status_code == 200

    async with AsyncClient(
        transport=ASGITransport(app=app_instance, client=("192.0.2.1", 50001)),
        base_url="https://dashboard.example",
        headers={"x-forwarded-for": "100.64.0.2", "x-forwarded-proto": "https"},
    ) as remote:
        login = await remote.post("/api/dashboard-auth/password/login", json={"password": "password123"})
        assert login.status_code == 200
        original_cookie = remote.cookies.get(DASHBOARD_SESSION_COOKIE)

        monkeypatch.setattr(auth_service, "time", lambda: issued_at + 12 * 60 * 60 + 1)
        after_twelve_hours = await remote.get("/api/settings")
        assert after_twelve_hours.status_code == 200

        monkeypatch.setattr(auth_service, "time", lambda: issued_at + configured_ttl - 1)
        before_expiry = await remote.get("/api/settings")
        assert before_expiry.status_code == 200
        assert "set-cookie" not in before_expiry.headers
        assert remote.cookies.get(DASHBOARD_SESSION_COOKIE) == original_cookie

        monkeypatch.setattr(auth_service, "time", lambda: issued_at + configured_ttl + 1)
        expired = await remote.get("/api/settings")
        assert expired.status_code == 401
        assert expired.json()["error"]["code"] == "authentication_required"
