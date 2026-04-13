from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.crypto import TokenEncryptor
from app.core.middleware.dashboard_auth_proxy import add_dashboard_auth_proxy_middleware
from app.core.usage.models import UsagePayload
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, ApiKeyLimit, DashboardSettings, LimitType, LimitWindow
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeysService
from app.modules.dashboard_auth.service import DASHBOARD_SESSION_COOKIE, get_dashboard_session_store

pytestmark = pytest.mark.integration


def _make_account(account_id: str, chatgpt_account_id: str, email: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        chatgpt_account_id=chatgpt_account_id,
        email=email,
        plan_type="team",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


async def _set_migration_inconsistent_totp_only_mode() -> None:
    async with SessionLocal() as session:
        settings = await session.get(DashboardSettings, 1)
        if settings is None:
            settings = DashboardSettings(
                id=1,
                sticky_threads_enabled=False,
                prefer_earlier_reset_accounts=False,
                totp_required_on_login=True,
                password_hash=None,
                api_key_auth_enabled=False,
                totp_secret_encrypted=None,
                totp_last_verified_step=None,
            )
            session.add(settings)
        else:
            settings.password_hash = None
            settings.totp_required_on_login = True
        await session.commit()
    await get_settings_cache().invalidate()


def _set_dashboard_auth_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: DashboardAuthMode,
    trust_proxy_headers: bool = False,
    trusted_proxy_cidrs: str = "127.0.0.1/32",
    proxy_header: str = "Remote-User",
) -> None:
    monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_MODE", mode)
    monkeypatch.setenv("CODEX_LB_FIREWALL_TRUST_PROXY_HEADERS", str(trust_proxy_headers).lower())
    monkeypatch.setenv("CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS", trusted_proxy_cidrs)
    monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_PROXY_HEADER", proxy_header)
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_session_branch_allows_without_password_and_blocks_without_session(async_client):
    public_mode = await async_client.get("/api/settings")
    assert public_mode.status_code == 200

    setup = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup.status_code == 200

    await async_client.post("/api/dashboard-auth/logout", json={})
    blocked = await async_client.get("/api/settings")
    assert blocked.status_code == 401
    assert blocked.json()["error"]["code"] == "authentication_required"

    login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "password123"},
    )
    assert login.status_code == 200
    allowed = await async_client.get("/api/settings")
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_remote_proxy_denied_before_auth_is_configured(app_instance):
    async with app_instance.router.lifespan_context(app_instance):
        transport = ASGITransport(app=app_instance, client=("203.0.113.11", 50001))
        async with AsyncClient(transport=transport, base_url="http://lb.example") as remote_client:
            response = await remote_client.get("/v1/models")
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "invalid_api_key"

            spoofed = await remote_client.get("/v1/models", headers={"Host": "localhost"})
            assert spoofed.status_code == 401
            assert spoofed.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_remote_first_run_requires_bootstrap_token(app_instance, monkeypatch):
    monkeypatch.setenv("CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN", "bootstrap-secret")
    from app.core.config.settings import get_settings
    from app.core.config.settings_cache import get_settings_cache

    get_settings.cache_clear()
    await get_settings_cache().invalidate()

    async with app_instance.router.lifespan_context(app_instance):
        transport = ASGITransport(app=app_instance, client=("203.0.113.10", 50000))
        async with AsyncClient(transport=transport, base_url="http://lb.example") as remote_client:
            session = await remote_client.get("/api/dashboard-auth/session")
            assert session.status_code == 200
            assert session.json()["authenticated"] is False
            assert session.json()["bootstrapRequired"] is True
            assert session.json()["bootstrapTokenConfigured"] is True

            protected_settings = await remote_client.get("/api/settings")
            assert protected_settings.status_code == 401
            assert protected_settings.json()["error"]["code"] == "bootstrap_required"

            spoofed_settings = await remote_client.get(
                "/api/settings",
                headers={"Host": "localhost"},
            )
            assert spoofed_settings.status_code == 401
            assert spoofed_settings.json()["error"]["code"] == "bootstrap_required"

            blocked = await remote_client.post(
                "/api/dashboard-auth/password/setup",
                json={"password": "password123"},
            )
            assert blocked.status_code == 401
            assert blocked.json()["error"]["code"] == "invalid_bootstrap_token"

            spoofed_session = await remote_client.get(
                "/api/dashboard-auth/session",
                headers={"Host": "localhost"},
            )
            assert spoofed_session.status_code == 200
            assert spoofed_session.json()["bootstrapRequired"] is True

            spoofed_blocked = await remote_client.post(
                "/api/dashboard-auth/password/setup",
                headers={"Host": "localhost"},
                json={"password": "password123"},
            )
            assert spoofed_blocked.status_code == 401
            assert spoofed_blocked.json()["error"]["code"] == "invalid_bootstrap_token"

            allowed = await remote_client.post(
                "/api/dashboard-auth/password/setup",
                json={"password": "password123", "bootstrapToken": "bootstrap-secret"},
            )
            assert allowed.status_code == 200

            protected_after = await remote_client.get("/api/settings")
            assert protected_after.status_code == 200


@pytest.mark.asyncio
async def test_trusted_header_mode_requires_proxy_header_for_open_dashboard(async_client, monkeypatch):
    _set_dashboard_auth_env(
        monkeypatch,
        mode=DashboardAuthMode.TRUSTED_HEADER,
        trust_proxy_headers=True,
    )

    session = await async_client.get("/api/dashboard-auth/session")
    assert session.status_code == 200
    assert session.json() == {
        "authenticated": False,
        "passwordRequired": False,
        "totpRequiredOnLogin": False,
        "totpConfigured": False,
        "bootstrapRequired": False,
        "bootstrapTokenConfigured": False,
        "authMode": "trusted_header",
        "passwordManagementEnabled": True,
        "passwordSessionActive": False,
    }

    blocked = await async_client.get("/api/settings")
    assert blocked.status_code == 401
    assert blocked.json()["error"]["code"] == "proxy_auth_required"


@pytest.mark.asyncio
async def test_trusted_header_mode_allows_proxy_header_and_password_fallback(async_client, monkeypatch):
    _set_dashboard_auth_env(
        monkeypatch,
        mode=DashboardAuthMode.TRUSTED_HEADER,
        trust_proxy_headers=True,
    )

    setup_without_proxy = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup_without_proxy.status_code == 401
    assert setup_without_proxy.json()["error"]["code"] == "proxy_auth_required"

    proxy_headers = {"Remote-User": "admin@example.com"}
    proxy_session = await async_client.get("/api/dashboard-auth/session", headers=proxy_headers)
    assert proxy_session.status_code == 200
    assert proxy_session.json()["authenticated"] is True
    assert proxy_session.json()["authMode"] == "trusted_header"

    allowed = await async_client.get("/api/settings", headers=proxy_headers)
    assert allowed.status_code == 200

    setup_with_proxy = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
        headers=proxy_headers,
    )
    assert setup_with_proxy.status_code == 200
    assert setup_with_proxy.json()["authMode"] == "trusted_header"

    async_client.cookies.clear()
    blocked = await async_client.get("/api/settings")
    assert blocked.status_code == 401
    assert blocked.json()["error"]["code"] == "authentication_required"

    fallback_login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "password123"},
    )
    assert fallback_login.status_code == 200
    assert fallback_login.json()["authMode"] == "trusted_header"

    allowed_with_password = await async_client.get("/api/settings")
    assert allowed_with_password.status_code == 200


@pytest.mark.asyncio
async def test_disabled_dashboard_auth_mode_bypasses_guard_and_disables_password_flows(async_client, monkeypatch):
    _set_dashboard_auth_env(monkeypatch, mode=DashboardAuthMode.DISABLED)

    session = await async_client.get("/api/dashboard-auth/session")
    assert session.status_code == 200
    assert session.json() == {
        "authenticated": True,
        "passwordRequired": False,
        "totpRequiredOnLogin": False,
        "totpConfigured": False,
        "bootstrapRequired": False,
        "bootstrapTokenConfigured": False,
        "authMode": "disabled",
        "passwordManagementEnabled": False,
        "passwordSessionActive": False,
    }

    allowed = await async_client.get("/api/settings")
    assert allowed.status_code == 200

    setup = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup.status_code == 400
    assert setup.json()["error"]["code"] == "password_management_disabled"

    login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "password123"},
    )
    assert login.status_code == 400
    assert login.json()["error"]["code"] == "password_management_disabled"

    start_totp = await async_client.post("/api/dashboard-auth/totp/setup/start", json={})
    assert start_totp.status_code == 400
    assert start_totp.json()["error"]["code"] == "password_management_disabled"

    disable_totp = await async_client.post("/api/dashboard-auth/totp/disable", json={"code": "123456"})
    assert disable_totp.status_code == 400
    assert disable_totp.json()["error"]["code"] == "password_management_disabled"


@pytest.mark.asyncio
async def test_trusted_header_proxy_auth_with_fallback_password_reports_no_active_session(async_client, monkeypatch):
    """Proxy-authenticated user with configured fallback password must see passwordSessionActive=False."""
    _set_dashboard_auth_env(
        monkeypatch,
        mode=DashboardAuthMode.TRUSTED_HEADER,
        trust_proxy_headers=True,
    )
    proxy_headers = {"Remote-User": "admin@example.com"}

    setup = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
        headers=proxy_headers,
    )
    assert setup.status_code == 200
    assert setup.json()["passwordSessionActive"] is True

    async_client.cookies.clear()

    session = await async_client.get("/api/dashboard-auth/session", headers=proxy_headers)
    assert session.status_code == 200
    body = session.json()
    assert body["authenticated"] is True
    assert body["authMode"] == "trusted_header"
    assert body["passwordRequired"] is True
    assert body["passwordManagementEnabled"] is True
    assert body["passwordSessionActive"] is False

    fallback_login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "password123"},
    )
    assert fallback_login.status_code == 200
    assert fallback_login.json()["passwordSessionActive"] is True

    session_after_login = await async_client.get("/api/dashboard-auth/session", headers=proxy_headers)
    assert session_after_login.json()["passwordSessionActive"] is True


@pytest.mark.asyncio
async def test_trusted_header_mode_scrubs_untrusted_proxy_header(monkeypatch):
    _set_dashboard_auth_env(
        monkeypatch,
        mode=DashboardAuthMode.TRUSTED_HEADER,
        trust_proxy_headers=True,
        trusted_proxy_cidrs="10.0.0.0/8",
    )
    app = FastAPI()
    add_dashboard_auth_proxy_middleware(app)

    @app.get("/dashboard-proxy-header")
    async def echo_dashboard_proxy_header(request: Request) -> dict[str, str | None]:
        return {"remote_user": request.headers.get("Remote-User")}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/dashboard-proxy-header",
            headers={"Remote-User": "attacker@example.com"},
        )

    assert response.status_code == 200
    assert response.json() == {"remote_user": None}


@pytest.mark.asyncio
async def test_totp_only_mode_requires_session_even_when_password_hash_is_null(async_client, caplog):
    await _set_migration_inconsistent_totp_only_mode()

    caplog.set_level(logging.WARNING, logger="app.core.auth.dependencies")
    blocked = await async_client.get("/api/settings")
    assert blocked.status_code == 401
    assert blocked.json()["error"]["code"] == "authentication_required"
    assert any("dashboard_auth_migration_inconsistency" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_totp_only_mode_accepts_totp_verified_session(async_client):
    await _set_migration_inconsistent_totp_only_mode()

    session_id = get_dashboard_session_store().create(password_verified=False, totp_verified=True)
    async_client.cookies.set(DASHBOARD_SESSION_COOKIE, session_id)

    allowed = await async_client.get("/api/settings")
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_totp_only_mode_rejects_missing_totp_verification(async_client):
    await _set_migration_inconsistent_totp_only_mode()

    session_id = get_dashboard_session_store().create(password_verified=True, totp_verified=False)
    async_client.cookies.set(DASHBOARD_SESSION_COOKIE, session_id)

    blocked = await async_client.get("/api/settings")
    assert blocked.status_code == 401
    assert blocked.json()["error"]["code"] == "totp_required"


@pytest.mark.asyncio
async def test_api_key_branch_disabled_then_enabled(async_client):
    disabled = await async_client.get("/v1/models")
    assert disabled.status_code == 200

    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    missing = await async_client.get("/v1/models")
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "invalid_api_key"

    async with SessionLocal() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        created = await service.create_key(
            ApiKeyCreateData(
                name="middleware-key",
                allowed_models=None,
                expires_at=None,
            )
        )

    invalid = await async_client.get("/v1/models", headers={"Authorization": "Bearer invalid-key"})
    assert invalid.status_code == 401
    assert invalid.json()["error"]["code"] == "invalid_api_key"

    valid = await async_client.get("/v1/models", headers={"Authorization": f"Bearer {created.key}"})
    assert valid.status_code == 200

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        row = await repo.get_by_id(created.id)
        assert row is not None
        row.expires_at = utcnow() - timedelta(seconds=1)
        await session.commit()

    expired = await async_client.get("/v1/models", headers={"Authorization": f"Bearer {created.key}"})
    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "invalid_api_key"

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        row = await repo.get_by_id(created.id)
        assert row is not None
        row.expires_at = None
        await session.commit()
        await repo.replace_limits(
            created.id,
            [
                ApiKeyLimit(
                    api_key_id=created.id,
                    limit_type=LimitType.TOTAL_TOKENS,
                    limit_window=LimitWindow.WEEKLY,
                    max_value=1,
                    current_value=1,
                    model_filter=None,
                    reset_at=utcnow() + timedelta(days=1),
                ),
            ],
        )

    over_limit = await async_client.get("/v1/models", headers={"Authorization": f"Bearer {created.key}"})
    assert over_limit.status_code == 429
    assert over_limit.json()["error"]["code"] == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_codex_usage_does_not_allow_dashboard_session_without_caller_identity(async_client):
    setup = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup.status_code == 200

    blocked = await async_client.get("/api/codex/usage")
    assert blocked.status_code == 401
    assert blocked.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_codex_usage_trailing_slash_uses_caller_identity_validation(async_client):
    setup = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup.status_code == 200

    await async_client.post("/api/dashboard-auth/logout", json={})
    blocked = await async_client.get("/api/codex/usage/")
    assert blocked.status_code == 401
    assert blocked.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_codex_usage_allows_registered_chatgpt_account_id_with_bearer(async_client, monkeypatch):
    setup = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup.status_code == 200

    raw_chatgpt_account_id = "workspace_shared"
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        # account.id can be extended while caller auth uses raw chatgpt_account_id.
        await repo.upsert(
            _make_account(
                "workspace_shared_a1b2c3d4",
                raw_chatgpt_account_id,
                "team-user@example.com",
            )
        )

    async def stub_fetch_usage(*, access_token: str, account_id: str | None, **_: object) -> UsagePayload:
        assert access_token == "chatgpt-token"
        assert account_id == raw_chatgpt_account_id
        return UsagePayload.model_validate({"plan_type": "team"})

    monkeypatch.setattr("app.core.auth.dependencies.fetch_usage", stub_fetch_usage)

    await async_client.post("/api/dashboard-auth/logout", json={})
    allowed = await async_client.get(
        "/api/codex/usage",
        headers={
            "Authorization": "Bearer chatgpt-token",
            "chatgpt-account-id": raw_chatgpt_account_id,
        },
    )
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_codex_usage_blocks_unregistered_chatgpt_account_id(async_client, monkeypatch):
    setup = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup.status_code == 200

    async def should_not_call_fetch_usage(**_: object) -> UsagePayload:
        raise AssertionError("fetch_usage should not be called for unknown chatgpt-account-id")

    monkeypatch.setattr("app.core.auth.dependencies.fetch_usage", should_not_call_fetch_usage)

    await async_client.post("/api/dashboard-auth/logout", json={})
    blocked = await async_client.get(
        "/api/codex/usage",
        headers={
            "Authorization": "Bearer chatgpt-token",
            "chatgpt-account-id": "workspace_missing",
        },
    )
    assert blocked.status_code == 401
    assert blocked.json()["error"]["code"] == "invalid_api_key"
