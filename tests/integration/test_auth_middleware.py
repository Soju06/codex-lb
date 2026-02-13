from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.utils.time import utcnow
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeysService

pytestmark = pytest.mark.integration


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
                weekly_token_limit=None,
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
        row.weekly_token_limit = 1
        row.weekly_tokens_used = 1
        row.weekly_reset_at = utcnow() + timedelta(days=1)
        await session.commit()

    over_limit = await async_client.get("/v1/models", headers={"Authorization": f"Bearer {created.key}"})
    assert over_limit.status_code == 429
    assert over_limit.json()["error"]["code"] == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_codex_usage_uses_dashboard_session_auth(async_client):
    setup = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup.status_code == 200

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

    authenticated = await async_client.get("/api/codex/usage")
    assert authenticated.status_code == 200

    await async_client.post("/api/dashboard-auth/logout", json={})
    blocked = await async_client.get("/api/codex/usage")
    assert blocked.status_code == 401
    assert blocked.json()["error"]["code"] == "authentication_required"
