from __future__ import annotations

import pytest

from app.modules.dashboard_auth.service import get_password_rate_limiter
from tests.support.auth import BOOTSTRAP_ADMIN_PASSWORD

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_password_endpoints_setup_login_change_remove(async_client):
    weak = await async_client.post("/api/dashboard-auth/password/setup", json={"password": "short"})
    assert weak.status_code == 422
    assert weak.json()["error"]["code"] == "validation_error"

    setup = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup.status_code == 409
    assert setup.json()["error"]["code"] == "password_already_configured"

    setup_again = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup_again.status_code == 409

    logout = await async_client.post("/api/dashboard-auth/logout", json={})
    assert logout.status_code == 200

    invalid_login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"username": "admin", "password": "wrong-password"},
    )
    assert invalid_login.status_code == 401
    assert invalid_login.json()["error"]["code"] == "invalid_credentials"

    login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"username": "admin", "password": BOOTSTRAP_ADMIN_PASSWORD},
    )
    assert login.status_code == 200
    assert login.json()["authenticated"] is True
    assert login.json()["user"]["username"] == "admin"

    bad_change = await async_client.post(
        "/api/dashboard-auth/password/change",
        json={"currentPassword": "wrong-password", "newPassword": "new-password-456"},
    )
    assert bad_change.status_code == 401
    assert bad_change.json()["error"]["code"] == "invalid_credentials"

    change = await async_client.post(
        "/api/dashboard-auth/password/change",
        json={"currentPassword": BOOTSTRAP_ADMIN_PASSWORD, "newPassword": "new-password-456"},
    )
    assert change.status_code == 200

    logout_again = await async_client.post("/api/dashboard-auth/logout", json={})
    assert logout_again.status_code == 200

    old_login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"username": "admin", "password": BOOTSTRAP_ADMIN_PASSWORD},
    )
    assert old_login.status_code == 401

    new_login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"username": "admin", "password": "new-password-456"},
    )
    assert new_login.status_code == 200

    bad_remove = await async_client.request(
        "DELETE",
        "/api/dashboard-auth/password",
        json={"password": "wrong-password"},
    )
    assert bad_remove.status_code == 400
    assert bad_remove.json()["error"]["code"] == "password_not_configured"

    remove = await async_client.request(
        "DELETE",
        "/api/dashboard-auth/password",
        json={"password": "new-password-456"},
    )
    assert remove.status_code == 400
    assert remove.json()["error"]["code"] == "password_not_configured"

    session = await async_client.get("/api/dashboard-auth/session")
    assert session.status_code == 200
    session_payload = session.json()
    assert session_payload["passwordRequired"] is True
    assert session_payload["authenticated"] is True
    assert session_payload["totpRequiredOnLogin"] is False


@pytest.mark.asyncio
async def test_password_login_rate_limit(async_client):
    limiter = get_password_rate_limiter()
    limiter._failures.clear()  # noqa: SLF001

    await async_client.post("/api/dashboard-auth/logout", json={})

    for _ in range(8):
        response = await async_client.post(
            "/api/dashboard-auth/password/login",
            json={"username": "admin", "password": "wrong-password"},
        )
        assert response.status_code == 401

    limited = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"username": "admin", "password": "wrong-password"},
    )
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers

    limiter._failures.clear()  # noqa: SLF001
    success = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"username": "admin", "password": BOOTSTRAP_ADMIN_PASSWORD},
    )
    assert success.status_code == 200
