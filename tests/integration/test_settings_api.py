from __future__ import annotations

import pytest
from sqlalchemy import text

from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_settings_api_get_and_update(async_client):
    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["stickyThreadsEnabled"] is True
    assert payload["upstreamStreamTransport"] == "default"
    assert payload["preferEarlierResetAccounts"] is True
    assert payload["preferEarlierResetWindow"] == "secondary"
    assert payload["routingStrategy"] == "capacity_weighted"
    assert payload["relativeAvailabilityPower"] == 2.0
    assert payload["relativeAvailabilityTopK"] == 5
    assert payload["singleAccountId"] is None
    assert payload["openaiCacheAffinityMaxAgeSeconds"] == 1800
    assert payload["dashboardSessionTtlSeconds"] == 43200
    assert payload["httpResponsesSessionBridgePromptCacheIdleTtlSeconds"] == 3600
    assert payload["httpResponsesSessionBridgeGatewaySafeMode"] is False
    assert payload["stickyReallocationBudgetThresholdPct"] == 95.0
    assert payload["stickyReallocationPrimaryBudgetThresholdPct"] == 95.0
    assert payload["stickyReallocationSecondaryBudgetThresholdPct"] == 100.0
    assert payload["importWithoutOverwrite"] is True
    assert payload["totpRequiredOnLogin"] is False
    assert payload["totpConfigured"] is False
    assert payload["apiKeyAuthEnabled"] is False
    assert payload["limitWarmupEnabled"] is False
    assert payload["limitWarmupWindows"] == "both"
    assert payload["limitWarmupModel"] == "auto"
    assert payload["limitWarmupPrompt"] == "Say OK."
    assert payload["limitWarmupCooldownSeconds"] == 3600
    assert payload["limitWarmupMinAvailablePercent"] == 100.0

    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "upstreamStreamTransport": "websocket",
            "preferEarlierResetAccounts": False,
            "routingStrategy": "relative_availability",
            "relativeAvailabilityPower": 1.5,
            "relativeAvailabilityTopK": 7,
            "preferEarlierResetWindow": "secondary",
            "singleAccountId": None,
            "openaiCacheAffinityMaxAgeSeconds": 180,
            "dashboardSessionTtlSeconds": 31536000,
            "httpResponsesSessionBridgePromptCacheIdleTtlSeconds": 1800,
            "httpResponsesSessionBridgeGatewaySafeMode": True,
            "stickyReallocationBudgetThresholdPct": 85.0,
            "stickyReallocationPrimaryBudgetThresholdPct": 85.0,
            "stickyReallocationSecondaryBudgetThresholdPct": 98.0,
            "importWithoutOverwrite": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
            "limitWarmupEnabled": True,
            "limitWarmupWindows": "primary",
            "limitWarmupModel": "gpt-5.1-codex-mini",
            "limitWarmupPrompt": "Say OK.",
            "limitWarmupCooldownSeconds": 7200,
            "limitWarmupMinAvailablePercent": 99.0,
        },
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["stickyThreadsEnabled"] is False
    assert updated["upstreamStreamTransport"] == "websocket"
    assert updated["preferEarlierResetAccounts"] is False
    assert updated["routingStrategy"] == "relative_availability"
    assert updated["relativeAvailabilityPower"] == 1.5
    assert updated["relativeAvailabilityTopK"] == 7
    assert updated["preferEarlierResetWindow"] == "secondary"
    assert updated["singleAccountId"] is None
    assert updated["openaiCacheAffinityMaxAgeSeconds"] == 180
    assert updated["dashboardSessionTtlSeconds"] == 31536000
    assert updated["httpResponsesSessionBridgePromptCacheIdleTtlSeconds"] == 1800
    assert updated["httpResponsesSessionBridgeGatewaySafeMode"] is True
    assert updated["stickyReallocationBudgetThresholdPct"] == 85.0
    assert updated["stickyReallocationPrimaryBudgetThresholdPct"] == 85.0
    assert updated["stickyReallocationSecondaryBudgetThresholdPct"] == 98.0
    assert updated["importWithoutOverwrite"] is False
    assert updated["totpRequiredOnLogin"] is False
    assert updated["totpConfigured"] is False
    assert updated["apiKeyAuthEnabled"] is True
    assert updated["limitWarmupEnabled"] is True
    assert updated["limitWarmupWindows"] == "primary"
    assert updated["limitWarmupModel"] == "gpt-5.1-codex-mini"
    assert updated["limitWarmupPrompt"] == "Say OK."
    assert updated["limitWarmupCooldownSeconds"] == 7200
    assert updated["limitWarmupMinAvailablePercent"] == 99.0

    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["stickyThreadsEnabled"] is False
    assert payload["upstreamStreamTransport"] == "websocket"
    assert payload["preferEarlierResetAccounts"] is False
    assert payload["routingStrategy"] == "relative_availability"
    assert payload["relativeAvailabilityPower"] == 1.5
    assert payload["relativeAvailabilityTopK"] == 7
    assert payload["preferEarlierResetWindow"] == "secondary"
    assert payload["singleAccountId"] is None
    assert payload["openaiCacheAffinityMaxAgeSeconds"] == 180
    assert payload["dashboardSessionTtlSeconds"] == 31536000
    assert payload["httpResponsesSessionBridgePromptCacheIdleTtlSeconds"] == 1800
    assert payload["httpResponsesSessionBridgeGatewaySafeMode"] is True
    assert payload["stickyReallocationBudgetThresholdPct"] == 85.0
    assert payload["stickyReallocationPrimaryBudgetThresholdPct"] == 85.0
    assert payload["stickyReallocationSecondaryBudgetThresholdPct"] == 98.0
    assert payload["importWithoutOverwrite"] is False
    assert payload["totpRequiredOnLogin"] is False
    assert payload["totpConfigured"] is False
    assert payload["apiKeyAuthEnabled"] is True
    assert payload["limitWarmupEnabled"] is True
    assert payload["limitWarmupWindows"] == "primary"
    assert payload["limitWarmupModel"] == "gpt-5.1-codex-mini"
    assert payload["limitWarmupPrompt"] == "Say OK."
    assert payload["limitWarmupCooldownSeconds"] == 7200
    assert payload["limitWarmupMinAvailablePercent"] == 99.0


@pytest.mark.asyncio
async def test_settings_api_accepts_fill_first_routing_strategy(async_client):
    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": True,
            "preferEarlierResetAccounts": True,
            "routingStrategy": "fill_first",
        },
    )
    assert response.status_code == 200
    assert response.json()["routingStrategy"] == "fill_first"

    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    assert response.json()["routingStrategy"] == "fill_first"


@pytest.mark.asyncio
async def test_settings_legacy_sticky_threshold_updates_primary_threshold(async_client):
    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": True,
            "preferEarlierResetAccounts": True,
            "stickyReallocationBudgetThresholdPct": 88.0,
        },
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["stickyReallocationBudgetThresholdPct"] == 88.0
    assert updated["stickyReallocationPrimaryBudgetThresholdPct"] == 88.0
    assert updated["stickyReallocationSecondaryBudgetThresholdPct"] == 100.0


@pytest.mark.asyncio
async def test_settings_primary_sticky_threshold_updates_legacy_threshold(async_client):
    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": True,
            "preferEarlierResetAccounts": True,
            "stickyReallocationPrimaryBudgetThresholdPct": 87.0,
        },
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["stickyReallocationBudgetThresholdPct"] == 87.0
    assert updated["stickyReallocationPrimaryBudgetThresholdPct"] == 87.0
    assert updated["stickyReallocationSecondaryBudgetThresholdPct"] == 100.0


@pytest.mark.asyncio
async def test_settings_api_rejects_unknown_routing_strategy(async_client):
    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": True,
            "preferEarlierResetAccounts": True,
            "routingStrategy": "fill_last",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_settings_full_put_rejects_conflicting_sticky_threshold_aliases(async_client):
    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    payload["stickyReallocationBudgetThresholdPct"] = 86.0

    response = await async_client.put("/api/settings", json=payload)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "conflicting_sticky_reallocation_thresholds"


@pytest.mark.asyncio
async def test_settings_full_put_allows_unrelated_save_with_divergent_sticky_thresholds(async_client):
    response = await async_client.get("/api/settings")
    assert response.status_code == 200

    async with SessionLocal() as session:
        await session.execute(
            text(
                """
                UPDATE dashboard_settings
                SET sticky_reallocation_budget_threshold_pct = 82.0,
                    sticky_reallocation_primary_budget_threshold_pct = 91.0
                WHERE id = 1
                """
            )
        )
        await session.commit()

    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["stickyReallocationBudgetThresholdPct"] == 82.0
    assert payload["stickyReallocationPrimaryBudgetThresholdPct"] == 91.0
    payload["importWithoutOverwrite"] = not payload["importWithoutOverwrite"]

    response = await async_client.put("/api/settings", json=payload)

    assert response.status_code == 200
    updated = response.json()
    assert updated["importWithoutOverwrite"] == payload["importWithoutOverwrite"]
    assert updated["stickyReallocationBudgetThresholdPct"] == 82.0
    assert updated["stickyReallocationPrimaryBudgetThresholdPct"] == 91.0


@pytest.mark.asyncio
async def test_settings_full_put_rejects_out_of_range_sticky_threshold(async_client):
    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    payload["stickyReallocationBudgetThresholdPct"] = 101.0

    response = await async_client.put("/api/settings", json=payload)

    assert response.status_code == 422
