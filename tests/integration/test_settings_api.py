from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_settings_api_get_and_update(async_client):
    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["stickyThreadsEnabled"] is True
    assert payload["upstreamStreamTransport"] == "default"
    assert payload["preferEarlierResetAccounts"] is True
    assert payload["routingStrategy"] == "capacity_weighted"
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
    assert payload["additionalQuotaRoutingPolicies"] == {}
    assert any(
        policy["quotaKey"] == "codex_spark" and policy["routingPolicy"] == "burn_first"
        for policy in payload["additionalQuotaPolicies"]
    )

    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "upstreamStreamTransport": "websocket",
            "preferEarlierResetAccounts": False,
            "routingStrategy": "round_robin",
            "openaiCacheAffinityMaxAgeSeconds": 180,
            "dashboardSessionTtlSeconds": 31536000,
            "httpResponsesSessionBridgePromptCacheIdleTtlSeconds": 1800,
            "httpResponsesSessionBridgeGatewaySafeMode": True,
            "stickyReallocationBudgetThresholdPct": 90.0,
            "stickyReallocationSecondaryBudgetThresholdPct": 98.0,
            "importWithoutOverwrite": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
            "additionalQuotaRoutingPolicies": {"codex_spark": "inherit"},
        },
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["stickyThreadsEnabled"] is False
    assert updated["upstreamStreamTransport"] == "websocket"
    assert updated["preferEarlierResetAccounts"] is False
    assert updated["routingStrategy"] == "round_robin"
    assert updated["openaiCacheAffinityMaxAgeSeconds"] == 180
    assert updated["dashboardSessionTtlSeconds"] == 31536000
    assert updated["httpResponsesSessionBridgePromptCacheIdleTtlSeconds"] == 1800
    assert updated["httpResponsesSessionBridgeGatewaySafeMode"] is True
    assert updated["stickyReallocationBudgetThresholdPct"] == 90.0
    assert updated["stickyReallocationPrimaryBudgetThresholdPct"] == 90.0
    assert updated["stickyReallocationSecondaryBudgetThresholdPct"] == 98.0
    assert updated["importWithoutOverwrite"] is False
    assert updated["totpRequiredOnLogin"] is False
    assert updated["totpConfigured"] is False
    assert updated["apiKeyAuthEnabled"] is True
    assert updated["additionalQuotaRoutingPolicies"] == {"codex_spark": "inherit"}
    assert any(
        policy["quotaKey"] == "codex_spark" and policy["routingPolicy"] == "inherit"
        for policy in updated["additionalQuotaPolicies"]
    )

    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["stickyThreadsEnabled"] is False
    assert payload["upstreamStreamTransport"] == "websocket"
    assert payload["preferEarlierResetAccounts"] is False
    assert payload["routingStrategy"] == "round_robin"
    assert payload["openaiCacheAffinityMaxAgeSeconds"] == 180
    assert payload["dashboardSessionTtlSeconds"] == 31536000
    assert payload["httpResponsesSessionBridgePromptCacheIdleTtlSeconds"] == 1800
    assert payload["httpResponsesSessionBridgeGatewaySafeMode"] is True
    assert payload["stickyReallocationBudgetThresholdPct"] == 90.0
    assert payload["stickyReallocationPrimaryBudgetThresholdPct"] == 90.0
    assert payload["stickyReallocationSecondaryBudgetThresholdPct"] == 98.0
    assert payload["importWithoutOverwrite"] is False
    assert payload["totpRequiredOnLogin"] is False
    assert payload["totpConfigured"] is False
    assert payload["apiKeyAuthEnabled"] is True
    assert payload["additionalQuotaRoutingPolicies"] == {"codex_spark": "inherit"}


@pytest.mark.asyncio
async def test_settings_api_rejects_unknown_additional_quota_routing_policy_key(async_client):
    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": True,
            "preferEarlierResetAccounts": True,
            "additionalQuotaRoutingPolicies": {"ghost_quota": "preserve"},
        },
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "invalid_additional_quota_routing_policies"
    assert "unknown quota keys: ghost_quota" in payload["error"]["message"]
    assert "valid quota keys:" in payload["error"]["message"]
    assert "valid routing policies:" in payload["error"]["message"]

    settings = await async_client.get("/api/settings")
    assert settings.status_code == 200
    assert settings.json()["additionalQuotaRoutingPolicies"] == {}


@pytest.mark.asyncio
async def test_settings_api_rejects_unknown_additional_quota_routing_policy_value(async_client):
    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": True,
            "preferEarlierResetAccounts": True,
            "additionalQuotaRoutingPolicies": {"codex_spark": "spend_fast"},
        },
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "invalid_additional_quota_routing_policies"
    assert "invalid routing policies: codex_spark=spend_fast" in payload["error"]["message"]
    assert "valid routing policies: burn_first, inherit, normal, preserve" in payload["error"]["message"]

    settings = await async_client.get("/api/settings")
    assert settings.status_code == 200
    assert settings.json()["additionalQuotaRoutingPolicies"] == {}
