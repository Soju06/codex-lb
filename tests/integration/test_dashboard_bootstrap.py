from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.core.bootstrap as bootstrap_module
import app.main as main_module
import app.modules.dashboard_auth.api as dashboard_auth_api_module
from app.core.bootstrap import ensure_auto_bootstrap_token, has_active_bootstrap_token
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache

pytestmark = pytest.mark.integration

_AUTO_BOOTSTRAP_TOKEN = "generated-bootstrap-token"
_ROTATED_BOOTSTRAP_TOKEN = "rotated-bootstrap-token"


@pytest_asyncio.fixture(autouse=True)
async def _reset_bootstrap_runtime(_reset_db_state, monkeypatch: pytest.MonkeyPatch):
    del _reset_db_state
    monkeypatch.delenv("CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN", raising=False)
    tokens = iter([_AUTO_BOOTSTRAP_TOKEN, _ROTATED_BOOTSTRAP_TOKEN, _AUTO_BOOTSTRAP_TOKEN, _ROTATED_BOOTSTRAP_TOKEN])
    monkeypatch.setattr(bootstrap_module.secrets, "token_urlsafe", lambda _size: next(tokens))
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

    assert await has_active_bootstrap_token() is True
    assert response.status_code == 200
    payload = response.json()
    assert payload["authenticated"] is False
    assert payload["bootstrapRequired"] is True
    assert payload["bootstrapTokenConfigured"] is True


@pytest.mark.asyncio
async def test_remote_bootstrap_with_auto_generated_token(async_client, monkeypatch):
    _force_remote(monkeypatch)

    response = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123", "bootstrapToken": _AUTO_BOOTSTRAP_TOKEN},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["authenticated"] is True
    assert await has_active_bootstrap_token() is False


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
async def test_remote_bootstrap_with_non_ascii_manual_env_token(async_client, monkeypatch):
    _force_remote(monkeypatch)
    monkeypatch.setenv("CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN", "부트스트랩-토큰")
    get_settings.cache_clear()
    await get_settings_cache().invalidate()

    response = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123", "bootstrapToken": "부트스트랩-토큰"},
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
    assert await has_active_bootstrap_token() is False

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
        assert await has_active_bootstrap_token() is True

    async with second_app.router.lifespan_context(second_app):
        transport = ASGITransport(app=second_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/dashboard-auth/password/setup",
                json={"password": "password123", "bootstrapToken": _ROTATED_BOOTSTRAP_TOKEN},
            )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_existing_auto_token_rotates_and_relogs_on_restart(monkeypatch: pytest.MonkeyPatch):
    _force_remote(monkeypatch)

    async def _noop_init_db() -> None:
        return None

    monkeypatch.setattr(main_module, "init_db", _noop_init_db)
    monkeypatch.setattr(main_module, "init_background_db", lambda: None)

    first_app = main_module.create_app()
    second_app = main_module.create_app()

    async with first_app.router.lifespan_context(first_app):
        pass

    assert await bootstrap_module.validate_bootstrap_token(_AUTO_BOOTSTRAP_TOKEN) is True

    async with second_app.router.lifespan_context(second_app):
        assert await bootstrap_module.validate_bootstrap_token(_AUTO_BOOTSTRAP_TOKEN) is False
        assert await bootstrap_module.validate_bootstrap_token(_ROTATED_BOOTSTRAP_TOKEN) is True


@pytest.mark.asyncio
async def test_remove_password_regenerates_bootstrap_token(async_client, monkeypatch):
    setup = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup.status_code == 200

    remove = await async_client.request(
        "DELETE",
        "/api/dashboard-auth/password",
        json={"password": "password123"},
    )
    assert remove.status_code == 200

    assert await has_active_bootstrap_token() is True

    _force_remote(monkeypatch)
    session = await async_client.get("/api/dashboard-auth/session")
    assert session.status_code == 200
    assert session.json()["bootstrapTokenConfigured"] is True

    resetup = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "new-password-456", "bootstrapToken": _ROTATED_BOOTSTRAP_TOKEN},
    )
    assert resetup.status_code == 200
