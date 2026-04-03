from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.db.session import SessionLocal
from app.modules.settings.repository import SettingsRepository

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_settings_api_get_and_update(async_client):
    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["stickyThreadsEnabled"] is False
    assert payload["upstreamStreamTransport"] == "default"
    assert payload["preferEarlierResetAccounts"] is False
    assert payload["routingStrategy"] == "capacity_weighted"
    assert payload["openaiCacheAffinityMaxAgeSeconds"] == 1800
    assert payload["httpResponsesSessionBridgePromptCacheIdleTtlSeconds"] == 3600
    assert payload["stickyReallocationBudgetThresholdPct"] == 95.0
    assert payload["importWithoutOverwrite"] is False
    assert payload["totpRequiredOnLogin"] is False
    assert payload["totpConfigured"] is False
    assert payload["apiKeyAuthEnabled"] is False
    assert payload["requestVisibilityMode"] == "off"
    assert payload["requestVisibilityExpiresAt"] is None
    assert payload["requestVisibilityEnabled"] is False

    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": True,
            "upstreamStreamTransport": "websocket",
            "preferEarlierResetAccounts": True,
            "routingStrategy": "round_robin",
            "openaiCacheAffinityMaxAgeSeconds": 180,
            "httpResponsesSessionBridgePromptCacheIdleTtlSeconds": 1800,
            "stickyReallocationBudgetThresholdPct": 90.0,
            "importWithoutOverwrite": True,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
            "requestVisibilityMode": "persistent",
        },
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["stickyThreadsEnabled"] is True
    assert updated["upstreamStreamTransport"] == "websocket"
    assert updated["preferEarlierResetAccounts"] is True
    assert updated["routingStrategy"] == "round_robin"
    assert updated["openaiCacheAffinityMaxAgeSeconds"] == 180
    assert updated["httpResponsesSessionBridgePromptCacheIdleTtlSeconds"] == 1800
    assert updated["stickyReallocationBudgetThresholdPct"] == 90.0
    assert updated["importWithoutOverwrite"] is True
    assert updated["totpRequiredOnLogin"] is False
    assert updated["totpConfigured"] is False
    assert updated["apiKeyAuthEnabled"] is True
    assert updated["requestVisibilityMode"] == "persistent"
    assert updated["requestVisibilityExpiresAt"] is None
    assert updated["requestVisibilityEnabled"] is True

    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["stickyThreadsEnabled"] is True
    assert payload["upstreamStreamTransport"] == "websocket"
    assert payload["preferEarlierResetAccounts"] is True
    assert payload["routingStrategy"] == "round_robin"
    assert payload["openaiCacheAffinityMaxAgeSeconds"] == 180
    assert payload["httpResponsesSessionBridgePromptCacheIdleTtlSeconds"] == 1800
    assert payload["stickyReallocationBudgetThresholdPct"] == 90.0
    assert payload["importWithoutOverwrite"] is True
    assert payload["totpRequiredOnLogin"] is False
    assert payload["totpConfigured"] is False
    assert payload["apiKeyAuthEnabled"] is True
    assert payload["requestVisibilityMode"] == "persistent"
    assert payload["requestVisibilityExpiresAt"] is None
    assert payload["requestVisibilityEnabled"] is True


@pytest.mark.asyncio
async def test_settings_api_temporary_request_visibility_expiry_disables_capture(async_client):
    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "requestVisibilityMode": "temporary",
            "requestVisibilityDurationMinutes": 1,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["requestVisibilityMode"] == "temporary"
    assert payload["requestVisibilityExpiresAt"] is not None
    assert payload["requestVisibilityEnabled"] is True

    expired_at = datetime.now(UTC) - timedelta(minutes=5)
    async with SessionLocal() as session:
        repo = SettingsRepository(session)
        await repo.update(
            request_visibility_mode="temporary",
            request_visibility_expires_at=expired_at,
        )
        await session.commit()

    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["requestVisibilityMode"] == "temporary"
    assert payload["requestVisibilityEnabled"] is False
    assert payload["requestVisibilityExpiresAt"] == expired_at.isoformat().replace("+00:00", "Z")
