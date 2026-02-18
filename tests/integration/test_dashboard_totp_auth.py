from __future__ import annotations

import asyncio

import pyotp
import pytest

from app.modules.dashboard_auth.service import DASHBOARD_SESSION_COOKIE, get_dashboard_session_store

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_cannot_enable_totp_requirement_without_configured_secret(async_client):
    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": True,
            "apiKeyAuthEnabled": False,
        },
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_totp_config"


@pytest.mark.asyncio
async def test_totp_setup_requires_password_session(async_client):
    response = await async_client.post("/api/dashboard-auth/totp/setup/start", json={})
    assert response.status_code == 401
    payload = response.json()
    assert payload["error"]["code"] == "authentication_required"


@pytest.mark.asyncio
async def test_totp_setup_rejects_stale_password_session_after_password_removal(async_client, monkeypatch):
    current_epoch = {"value": 1_700_000_000}

    import app.core.auth.totp as totp_module
    import app.modules.dashboard_auth.service as dashboard_auth_service_module

    monkeypatch.setattr(totp_module, "time", lambda: current_epoch["value"])
    monkeypatch.setattr(dashboard_auth_service_module, "time", lambda: current_epoch["value"])

    setup_password = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup_password.status_code == 200
    stale_session = async_client.cookies.get(DASHBOARD_SESSION_COOKIE)
    assert isinstance(stale_session, str) and stale_session

    remove = await async_client.request(
        "DELETE",
        "/api/dashboard-auth/password",
        json={"password": "password123"},
    )
    assert remove.status_code == 200
    assert async_client.cookies.get(DASHBOARD_SESSION_COOKIE) is None

    async_client.cookies.set(DASHBOARD_SESSION_COOKIE, stale_session)

    start = await async_client.post("/api/dashboard-auth/totp/setup/start", json={})
    assert start.status_code == 401
    assert start.json()["error"]["code"] == "authentication_required"

    secret = "JBSWY3DPEHPK3PXP"
    code = pyotp.TOTP(secret).at(current_epoch["value"])
    confirm = await async_client.post(
        "/api/dashboard-auth/totp/setup/confirm",
        json={"secret": secret, "code": code},
    )
    assert confirm.status_code == 401
    assert confirm.json()["error"]["code"] == "authentication_required"

    settings = await async_client.get("/api/settings")
    assert settings.status_code == 200
    assert settings.json()["totpConfigured"] is False


@pytest.mark.asyncio
async def test_dashboard_password_and_totp_flow(async_client, monkeypatch):
    current_epoch = {"value": 1_700_000_000}

    import app.core.auth.totp as totp_module
    import app.modules.dashboard_auth.service as dashboard_auth_service_module

    monkeypatch.setattr(totp_module, "time", lambda: current_epoch["value"])
    monkeypatch.setattr(dashboard_auth_service_module, "time", lambda: current_epoch["value"])

    setup_password = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup_password.status_code == 200
    assert setup_password.json()["authenticated"] is True

    start = await async_client.post("/api/dashboard-auth/totp/setup/start", json={})
    assert start.status_code == 200
    setup_payload = start.json()
    secret = setup_payload["secret"]
    assert isinstance(setup_payload["qrSvgDataUri"], str)
    assert setup_payload["qrSvgDataUri"].startswith("data:image/svg+xml;base64,")

    setup_code = pyotp.TOTP(secret).at(current_epoch["value"])
    confirm = await async_client.post(
        "/api/dashboard-auth/totp/setup/confirm",
        json={"secret": secret, "code": setup_code},
    )
    assert confirm.status_code == 200

    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": True,
            "apiKeyAuthEnabled": False,
        },
    )
    assert enable.status_code == 200
    enabled_payload = enable.json()
    assert enabled_payload["totpRequiredOnLogin"] is True
    assert enabled_payload["totpConfigured"] is True

    logout = await async_client.post("/api/dashboard-auth/logout", json={})
    assert logout.status_code == 200

    session = await async_client.get("/api/dashboard-auth/session")
    assert session.status_code == 200
    session_payload = session.json()
    assert session_payload["authenticated"] is False
    assert session_payload["passwordRequired"] is True
    assert session_payload["totpRequiredOnLogin"] is False

    blocked = await async_client.get("/api/settings")
    assert blocked.status_code == 401
    blocked_payload = blocked.json()
    assert blocked_payload["error"]["code"] == "authentication_required"

    login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "password123"},
    )
    assert login.status_code == 200
    login_payload = login.json()
    assert login_payload["authenticated"] is False
    assert login_payload["totpRequiredOnLogin"] is True

    verify_code = pyotp.TOTP(secret).at(current_epoch["value"])
    verify = await async_client.post(
        "/api/dashboard-auth/totp/verify",
        json={"code": verify_code},
    )
    assert verify.status_code == 200
    assert verify.json()["authenticated"] is True

    allowed = await async_client.get("/api/settings")
    assert allowed.status_code == 200

    current_epoch["value"] += 30
    disable_code = pyotp.TOTP(secret).at(current_epoch["value"])
    disable = await async_client.post("/api/dashboard-auth/totp/disable", json={"code": disable_code})
    assert disable.status_code == 200

    settings = await async_client.get("/api/settings")
    assert settings.status_code == 200
    settings_payload = settings.json()
    assert settings_payload["totpConfigured"] is False
    assert settings_payload["totpRequiredOnLogin"] is False


@pytest.mark.asyncio
async def test_disable_totp_requires_totp_verified_session(async_client, monkeypatch):
    current_epoch = {"value": 1_700_000_000}

    import app.core.auth.totp as totp_module
    import app.modules.dashboard_auth.service as dashboard_auth_service_module

    monkeypatch.setattr(totp_module, "time", lambda: current_epoch["value"])
    monkeypatch.setattr(dashboard_auth_service_module, "time", lambda: current_epoch["value"])

    setup_password = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup_password.status_code == 200

    start = await async_client.post("/api/dashboard-auth/totp/setup/start", json={})
    assert start.status_code == 200
    secret = start.json()["secret"]
    setup_code = pyotp.TOTP(secret).at(current_epoch["value"])
    confirm = await async_client.post(
        "/api/dashboard-auth/totp/setup/confirm",
        json={"secret": secret, "code": setup_code},
    )
    assert confirm.status_code == 200

    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": True,
            "apiKeyAuthEnabled": False,
        },
    )
    assert enable.status_code == 200

    await async_client.post("/api/dashboard-auth/logout", json={})
    login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "password123"},
    )
    assert login.status_code == 200

    disable = await async_client.post("/api/dashboard-auth/totp/disable", json={"code": setup_code})
    assert disable.status_code == 401
    assert disable.json()["error"]["code"] == "authentication_required"


@pytest.mark.asyncio
async def test_disable_totp_rejects_replayed_step_code(async_client, monkeypatch):
    current_epoch = {"value": 1_700_000_000}

    import app.core.auth.totp as totp_module
    import app.modules.dashboard_auth.service as dashboard_auth_service_module

    monkeypatch.setattr(totp_module, "time", lambda: current_epoch["value"])
    monkeypatch.setattr(dashboard_auth_service_module, "time", lambda: current_epoch["value"])

    setup_password = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup_password.status_code == 200

    start = await async_client.post("/api/dashboard-auth/totp/setup/start", json={})
    assert start.status_code == 200
    secret = start.json()["secret"]
    setup_code = pyotp.TOTP(secret).at(current_epoch["value"])
    confirm = await async_client.post(
        "/api/dashboard-auth/totp/setup/confirm",
        json={"secret": secret, "code": setup_code},
    )
    assert confirm.status_code == 200

    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": True,
            "apiKeyAuthEnabled": False,
        },
    )
    assert enable.status_code == 200

    await async_client.post("/api/dashboard-auth/logout", json={})
    login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "password123"},
    )
    assert login.status_code == 200

    verify_code = pyotp.TOTP(secret).at(current_epoch["value"])
    verify = await async_client.post(
        "/api/dashboard-auth/totp/verify",
        json={"code": verify_code},
    )
    assert verify.status_code == 200

    replay_disable = await async_client.post("/api/dashboard-auth/totp/disable", json={"code": verify_code})
    assert replay_disable.status_code == 400
    assert replay_disable.json()["error"]["code"] == "invalid_totp_code"


@pytest.mark.asyncio
async def test_disable_totp_requires_existing_totp_configuration(async_client):
    setup_password = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup_password.status_code == 200

    session_id = get_dashboard_session_store().create(password_verified=True, totp_verified=True)
    async_client.cookies.set(DASHBOARD_SESSION_COOKIE, session_id)

    disable = await async_client.post("/api/dashboard-auth/totp/disable", json={"code": "123456"})
    assert disable.status_code == 400
    assert disable.json()["error"]["code"] == "invalid_totp_code"


@pytest.mark.asyncio
async def test_password_management_requires_totp_when_totp_required(async_client, monkeypatch):
    current_epoch = {"value": 1_700_000_000}

    import app.core.auth.totp as totp_module
    import app.modules.dashboard_auth.service as dashboard_auth_service_module

    monkeypatch.setattr(totp_module, "time", lambda: current_epoch["value"])
    monkeypatch.setattr(dashboard_auth_service_module, "time", lambda: current_epoch["value"])

    setup_password = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup_password.status_code == 200

    start = await async_client.post("/api/dashboard-auth/totp/setup/start", json={})
    assert start.status_code == 200
    secret = start.json()["secret"]

    setup_code = pyotp.TOTP(secret).at(current_epoch["value"])
    confirm = await async_client.post(
        "/api/dashboard-auth/totp/setup/confirm",
        json={"secret": secret, "code": setup_code},
    )
    assert confirm.status_code == 200

    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": True,
            "apiKeyAuthEnabled": False,
        },
    )
    assert enable.status_code == 200

    logout = await async_client.post("/api/dashboard-auth/logout", json={})
    assert logout.status_code == 200

    login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "password123"},
    )
    assert login.status_code == 200

    blocked_change = await async_client.post(
        "/api/dashboard-auth/password/change",
        json={"currentPassword": "password123", "newPassword": "new-password-456"},
    )
    assert blocked_change.status_code == 401
    assert blocked_change.json()["error"]["code"] == "totp_required"

    blocked_remove = await async_client.request(
        "DELETE",
        "/api/dashboard-auth/password",
        json={"password": "password123"},
    )
    assert blocked_remove.status_code == 401
    assert blocked_remove.json()["error"]["code"] == "totp_required"

    verify_code = pyotp.TOTP(secret).at(current_epoch["value"])
    verify = await async_client.post(
        "/api/dashboard-auth/totp/verify",
        json={"code": verify_code},
    )
    assert verify.status_code == 200

    allowed_change = await async_client.post(
        "/api/dashboard-auth/password/change",
        json={"currentPassword": "password123", "newPassword": "new-password-456"},
    )
    assert allowed_change.status_code == 200


@pytest.mark.asyncio
async def test_verify_rejects_one_of_concurrent_replays(async_client, monkeypatch):
    current_epoch = {"value": 1_700_000_000}

    import app.core.auth.totp as totp_module
    import app.modules.dashboard_auth.repository as dashboard_auth_repository_module
    import app.modules.dashboard_auth.service as dashboard_auth_service_module

    monkeypatch.setattr(totp_module, "time", lambda: current_epoch["value"])
    monkeypatch.setattr(dashboard_auth_service_module, "time", lambda: current_epoch["value"])

    setup_password = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup_password.status_code == 200

    original_try_advance = dashboard_auth_repository_module.DashboardAuthRepository.try_advance_totp_last_verified_step

    async def delayed_try_advance(self, step: int) -> bool:
        await asyncio.sleep(0.05)
        return await original_try_advance(self, step)

    monkeypatch.setattr(
        dashboard_auth_repository_module.DashboardAuthRepository,
        "try_advance_totp_last_verified_step",
        delayed_try_advance,
    )

    start = await async_client.post("/api/dashboard-auth/totp/setup/start", json={})
    assert start.status_code == 200
    secret = start.json()["secret"]

    setup_code = pyotp.TOTP(secret).at(current_epoch["value"])
    confirm = await async_client.post(
        "/api/dashboard-auth/totp/setup/confirm",
        json={"secret": secret, "code": setup_code},
    )
    assert confirm.status_code == 200

    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": True,
            "apiKeyAuthEnabled": False,
        },
    )
    assert enable.status_code == 200

    logout = await async_client.post("/api/dashboard-auth/logout", json={})
    assert logout.status_code == 200

    login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "password123"},
    )
    assert login.status_code == 200

    verify_code = pyotp.TOTP(secret).at(current_epoch["value"])
    first, second = await asyncio.gather(
        async_client.post("/api/dashboard-auth/totp/verify", json={"code": verify_code}),
        async_client.post("/api/dashboard-auth/totp/verify", json={"code": verify_code}),
    )
    assert sorted([first.status_code, second.status_code]) == [200, 400]
