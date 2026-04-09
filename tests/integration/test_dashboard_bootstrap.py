from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.core.bootstrap as bootstrap_module
import app.main as main_module
import app.modules.dashboard_auth.api as dashboard_auth_api_module
from app.core.bootstrap import ensure_auto_bootstrap_token, get_active_bootstrap_token
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(autouse=True)
async def _reset_bootstrap_runtime(_reset_db_state, monkeypatch: pytest.MonkeyPatch):
    del _reset_db_state
    monkeypatch.delenv("CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN", raising=False)
    get_settings.cache_clear()
    await get_settings_cache().invalidate()
    await bootstrap_module.clear_auto_generated_token()
    yield
    monkeypatch.delenv("CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN", raising=False)
    get_settings.cache_clear()
    await get_settings_cache().invalidate()
    await bootstrap_module.clear_auto_generated_token()


@pytest_asyncio.fixture
async def app_instance(_reset_db_state, monkeypatch: pytest.MonkeyPatch):
    del _reset_db_state

    async def _noop_init_db() -> None:
        return None

    monkeypatch.setattr(main_module, "init_db", _noop_init_db)
    monkeypatch.setattr(main_module, "init_background_db", lambda: None)
    return main_module.create_app()


def _force_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard_auth_api_module, "is_local_request", lambda _request: False)


@pytest.mark.asyncio
async def test_session_reports_bootstrap_token_configured_true_with_auto_token(async_client, monkeypatch):
    _force_remote(monkeypatch)

    response = await async_client.get("/api/dashboard-auth/session")

    assert await get_active_bootstrap_token()
    assert response.status_code == 200
    payload = response.json()
    assert payload["authenticated"] is False
    assert payload["bootstrapRequired"] is True
    assert payload["bootstrapTokenConfigured"] is True


@pytest.mark.asyncio
async def test_remote_bootstrap_with_auto_generated_token(async_client, monkeypatch):
    _force_remote(monkeypatch)
    token = await get_active_bootstrap_token()

    response = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123", "bootstrapToken": token},
    )

    assert isinstance(token, str) and token
    assert response.status_code == 200
    payload = response.json()
    assert payload["authenticated"] is True
    assert await get_active_bootstrap_token() is None


@pytest.mark.asyncio
async def test_remote_bootstrap_with_manual_env_token(async_client, monkeypatch):
    _force_remote(monkeypatch)
    monkeypatch.setenv("CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN", "manual-env-token")
    get_settings.cache_clear()
    await get_settings_cache().invalidate()

    response = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123", "bootstrapToken": "manual-env-token"},
    )

    assert response.status_code == 200
    assert response.json()["authenticated"] is True


@pytest.mark.asyncio
async def test_remote_bootstrap_rejects_wrong_token(async_client, monkeypatch):
    _force_remote(monkeypatch)

    response = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123", "bootstrapToken": "wrong-token"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_bootstrap_token"


@pytest.mark.asyncio
async def test_no_token_generated_when_password_exists(async_client, monkeypatch):
    setup = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )

    assert setup.status_code == 200
    assert await get_active_bootstrap_token() is None

    settings = await get_settings_cache().get()
    assert settings.password_hash is not None
    assert await ensure_auto_bootstrap_token() is None

    _force_remote(monkeypatch)
    session = await async_client.get("/api/dashboard-auth/session")

    assert session.status_code == 200
    payload = session.json()
    assert payload["passwordRequired"] is True
    assert payload["bootstrapRequired"] is False
    assert payload["bootstrapTokenConfigured"] is False


@pytest.mark.asyncio
async def test_auto_generated_token_is_shared_across_app_instances(monkeypatch: pytest.MonkeyPatch):
    _force_remote(monkeypatch)

    async def _noop_init_db() -> None:
        return None

    monkeypatch.setattr(main_module, "init_db", _noop_init_db)
    monkeypatch.setattr(main_module, "init_background_db", lambda: None)

    first_app = main_module.create_app()
    second_app = main_module.create_app()

    async with first_app.router.lifespan_context(first_app):
        first_token = await get_active_bootstrap_token()

    async with second_app.router.lifespan_context(second_app):
        second_token = await get_active_bootstrap_token()
        transport = ASGITransport(app=second_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/dashboard-auth/password/setup",
                json={"password": "password123", "bootstrapToken": first_token},
            )

    assert isinstance(first_token, str) and first_token
    assert first_token == second_token
    assert response.status_code == 200
