from __future__ import annotations

import asyncio

import pytest

from app.core.auth.totp import generate_totp_code

pytestmark = pytest.mark.integration

_SETUP_TOKEN_HEADER = "X-Codex-LB-Setup-Token"


@pytest.mark.asyncio
async def test_cannot_enable_totp_requirement_without_configured_secret(async_client):
    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": True,
        },
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_totp_config"


@pytest.mark.asyncio
async def test_setup_start_requires_token_when_not_configured(async_client, monkeypatch):
    monkeypatch.delenv("CODEX_LB_DASHBOARD_SETUP_TOKEN", raising=False)
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    response = await async_client.post(
        "/api/dashboard-auth/totp/setup/start",
        json={},
    )
    assert response.status_code == 403
    payload = response.json()
    assert payload["error"]["code"] == "dashboard_setup_token_required"


@pytest.mark.asyncio
async def test_setup_start_requires_token_for_loopback_client_when_configured(async_client, monkeypatch):
    monkeypatch.setenv("CODEX_LB_DASHBOARD_SETUP_TOKEN", "test-setup-token")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    response = await async_client.post(
        "/api/dashboard-auth/totp/setup/start",
        json={},
    )
    assert response.status_code == 403
    payload = response.json()
    assert payload["error"]["code"] == "dashboard_setup_forbidden"


@pytest.mark.asyncio
async def test_setup_confirm_rejects_invalid_secret(async_client, monkeypatch):
    monkeypatch.setenv("CODEX_LB_DASHBOARD_SETUP_TOKEN", "test-setup-token")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    response = await async_client.post(
        "/api/dashboard-auth/totp/setup/confirm",
        json={"secret": "%%%%", "code": "123456"},
        headers={_SETUP_TOKEN_HEADER: "test-setup-token"},
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_totp_setup"


@pytest.mark.asyncio
async def test_dashboard_totp_flow_enforces_auth_per_login(async_client, monkeypatch):
    current_epoch = {"value": 1_700_000_000}

    import app.core.auth.totp as totp_module
    import app.modules.dashboard_auth.service as dashboard_auth_service_module

    monkeypatch.setattr(totp_module, "time", lambda: current_epoch["value"])
    monkeypatch.setattr(dashboard_auth_service_module, "time", lambda: current_epoch["value"])

    monkeypatch.setenv("CODEX_LB_DASHBOARD_SETUP_TOKEN", "test-setup-token")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    start = await async_client.post(
        "/api/dashboard-auth/totp/setup/start",
        json={},
        headers={_SETUP_TOKEN_HEADER: "test-setup-token"},
    )
    assert start.status_code == 200
    setup_payload = start.json()
    secret = setup_payload["secret"]
    assert isinstance(setup_payload["qrSvgDataUri"], str)
    assert setup_payload["qrSvgDataUri"].startswith("data:image/svg+xml;base64,")

    setup_code = generate_totp_code(secret)
    confirm = await async_client.post(
        "/api/dashboard-auth/totp/setup/confirm",
        json={"secret": secret, "code": setup_code},
        headers={_SETUP_TOKEN_HEADER: "test-setup-token"},
    )
    assert confirm.status_code == 200

    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": True,
        },
    )
    assert enable.status_code == 200
    enabled_payload = enable.json()
    assert enabled_payload["totpRequiredOnLogin"] is True
    assert enabled_payload["totpConfigured"] is True

    blocked = await async_client.get("/api/settings")
    assert blocked.status_code == 401
    blocked_payload = blocked.json()
    assert blocked_payload["error"]["code"] == "totp_required"

    session = await async_client.get("/api/dashboard-auth/session")
    assert session.status_code == 200
    session_payload = session.json()
    assert session_payload["authenticated"] is False
    assert session_payload["totpRequiredOnLogin"] is True

    verify_code = generate_totp_code(secret)
    verify = await async_client.post(
        "/api/dashboard-auth/totp/verify",
        json={"code": verify_code},
    )
    assert verify.status_code == 200
    verified_payload = verify.json()
    assert verified_payload["authenticated"] is True

    replay = await async_client.post(
        "/api/dashboard-auth/totp/verify",
        json={"code": verify_code},
    )
    assert replay.status_code == 400

    allowed = await async_client.get("/api/settings")
    assert allowed.status_code == 200

    logout = await async_client.post("/api/dashboard-auth/logout", json={})
    assert logout.status_code == 200

    blocked_again = await async_client.get("/api/settings")
    assert blocked_again.status_code == 401

    current_epoch["value"] += 60
    reverify_code = generate_totp_code(secret)
    reverify = await async_client.post("/api/dashboard-auth/totp/verify", json={"code": reverify_code})
    assert reverify.status_code == 200

    disable = await async_client.post("/api/dashboard-auth/totp/disable", json={"code": reverify_code})
    assert disable.status_code == 200

    allowed_again = await async_client.get("/api/settings")
    assert allowed_again.status_code == 200


@pytest.mark.asyncio
async def test_verify_rejects_one_of_concurrent_replays(async_client, monkeypatch):
    current_epoch = {"value": 1_700_000_000}

    import app.core.auth.totp as totp_module
    import app.modules.dashboard_auth.repository as dashboard_auth_repository_module
    import app.modules.dashboard_auth.service as dashboard_auth_service_module
    from app.core.config.settings import get_settings

    monkeypatch.setattr(totp_module, "time", lambda: current_epoch["value"])
    monkeypatch.setattr(dashboard_auth_service_module, "time", lambda: current_epoch["value"])

    monkeypatch.setenv("CODEX_LB_DASHBOARD_SETUP_TOKEN", "test-setup-token")
    get_settings.cache_clear()

    original_try_advance = dashboard_auth_repository_module.DashboardAuthRepository.try_advance_totp_last_verified_step

    async def delayed_try_advance(self, step: int) -> bool:
        await asyncio.sleep(0.05)
        return await original_try_advance(self, step)

    monkeypatch.setattr(
        dashboard_auth_repository_module.DashboardAuthRepository,
        "try_advance_totp_last_verified_step",
        delayed_try_advance,
    )

    start = await async_client.post(
        "/api/dashboard-auth/totp/setup/start",
        json={},
        headers={_SETUP_TOKEN_HEADER: "test-setup-token"},
    )
    assert start.status_code == 200
    secret = start.json()["secret"]

    setup_code = generate_totp_code(secret)
    confirm = await async_client.post(
        "/api/dashboard-auth/totp/setup/confirm",
        json={"secret": secret, "code": setup_code},
        headers={_SETUP_TOKEN_HEADER: "test-setup-token"},
    )
    assert confirm.status_code == 200

    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": True,
        },
    )
    assert enable.status_code == 200

    verify_code = generate_totp_code(secret)
    first, second = await asyncio.gather(
        async_client.post("/api/dashboard-auth/totp/verify", json={"code": verify_code}),
        async_client.post("/api/dashboard-auth/totp/verify", json={"code": verify_code}),
    )
    assert sorted([first.status_code, second.status_code]) == [200, 400]
