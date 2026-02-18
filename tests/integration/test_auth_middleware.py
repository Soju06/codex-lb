from __future__ import annotations

import logging
from datetime import timedelta

import pytest

from app.core.config.settings_cache import get_settings_cache
from app.core.crypto import TokenEncryptor
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
