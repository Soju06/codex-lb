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
    assert payload["upstreamProxyRoutingEnabled"] is False
    assert payload["upstreamProxyDefaultPoolId"] is None
    assert payload["preferEarlierResetAccounts"] is True
    assert payload["routingStrategy"] == "capacity_weighted"
    assert payload["relativeAvailabilityPower"] == 2.0
    assert payload["relativeAvailabilityTopK"] == 5
    assert payload["openaiCacheAffinityMaxAgeSeconds"] == 1800
    assert payload["dashboardSessionTtlSeconds"] == 43200
    assert payload["httpResponsesSessionBridgePromptCacheIdleTtlSeconds"] == 3600
    assert payload["httpResponsesSessionBridgeGatewaySafeMode"] is False
    assert payload["stickyReallocationBudgetThresholdPct"] == 95.0
    assert payload["warmupModel"] == "gpt-5.4-mini"
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
            "upstreamProxyRoutingEnabled": True,
            "upstreamProxyDefaultPoolId": None,
            "preferEarlierResetAccounts": False,
            "routingStrategy": "relative_availability",
            "relativeAvailabilityPower": 1.5,
            "relativeAvailabilityTopK": 7,
            "openaiCacheAffinityMaxAgeSeconds": 180,
            "dashboardSessionTtlSeconds": 31536000,
            "httpResponsesSessionBridgePromptCacheIdleTtlSeconds": 1800,
            "httpResponsesSessionBridgeGatewaySafeMode": True,
            "stickyReallocationBudgetThresholdPct": 90.0,
            "warmupModel": "gpt-5.4-nano",
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
    assert updated["upstreamProxyRoutingEnabled"] is True
    assert updated["upstreamProxyDefaultPoolId"] is None
    assert updated["preferEarlierResetAccounts"] is False
    assert updated["routingStrategy"] == "relative_availability"
    assert updated["relativeAvailabilityPower"] == 1.5
    assert updated["relativeAvailabilityTopK"] == 7
    assert updated["openaiCacheAffinityMaxAgeSeconds"] == 180
    assert updated["dashboardSessionTtlSeconds"] == 31536000
    assert updated["httpResponsesSessionBridgePromptCacheIdleTtlSeconds"] == 1800
    assert updated["httpResponsesSessionBridgeGatewaySafeMode"] is True
    assert updated["stickyReallocationBudgetThresholdPct"] == 90.0
    assert updated["warmupModel"] == "gpt-5.4-nano"
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
    assert payload["upstreamProxyRoutingEnabled"] is True
    assert payload["upstreamProxyDefaultPoolId"] is None
    assert payload["preferEarlierResetAccounts"] is False
    assert payload["routingStrategy"] == "relative_availability"
    assert payload["relativeAvailabilityPower"] == 1.5
    assert payload["relativeAvailabilityTopK"] == 7
    assert payload["openaiCacheAffinityMaxAgeSeconds"] == 180
    assert payload["dashboardSessionTtlSeconds"] == 31536000
    assert payload["httpResponsesSessionBridgePromptCacheIdleTtlSeconds"] == 1800
    assert payload["httpResponsesSessionBridgeGatewaySafeMode"] is True
    assert payload["stickyReallocationBudgetThresholdPct"] == 90.0
    assert payload["warmupModel"] == "gpt-5.4-nano"
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
async def test_settings_api_allows_partial_updates(async_client):
    original_response = await async_client.get("/api/settings")
    assert original_response.status_code == 200
    original = original_response.json()

    response = await async_client.put(
        "/api/settings",
        json={"warmupModel": "gpt-5.4-pro"},
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["warmupModel"] == "gpt-5.4-pro"
    assert updated["stickyThreadsEnabled"] == original["stickyThreadsEnabled"]
    assert updated["preferEarlierResetAccounts"] == original["preferEarlierResetAccounts"]
    assert updated["routingStrategy"] == original["routingStrategy"]
    assert updated["upstreamProxyRoutingEnabled"] == original["upstreamProxyRoutingEnabled"]
    assert updated["upstreamProxyDefaultPoolId"] == original["upstreamProxyDefaultPoolId"]


async def test_upstream_proxy_admin_controls(async_client):
    endpoint = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={
            "name": "Proxy A",
            "scheme": "http",
            "host": "proxy.internal",
            "port": 8080,
            "username": "user",
            "password": "secret",
        },
    )
    assert endpoint.status_code == 200
    endpoint_payload = endpoint.json()
    assert endpoint_payload["host"] == "proxy.internal"
    assert "password" not in endpoint_payload

    pool = await async_client.post(
        "/api/settings/upstream-proxy/pools",
        json={"name": "Pool A", "endpointIds": [endpoint_payload["id"]]},
    )
    assert pool.status_code == 200
    pool_payload = pool.json()
    assert pool_payload["endpointIds"] == [endpoint_payload["id"]]

    settings = await async_client.get("/api/settings")
    body = settings.json()
    body["upstreamProxyRoutingEnabled"] = True
    body["upstreamProxyDefaultPoolId"] = pool_payload["id"]
    updated = await async_client.put("/api/settings", json=body)
    assert updated.status_code == 200
    assert updated.json()["upstreamProxyDefaultPoolId"] == pool_payload["id"]

    body["upstreamProxyDefaultPoolId"] = None
    cleared = await async_client.put("/api/settings", json=body)
    assert cleared.status_code == 200
    assert cleared.json()["upstreamProxyDefaultPoolId"] is None

    body["upstreamProxyDefaultPoolId"] = pool_payload["id"]
    updated = await async_client.put("/api/settings", json=body)
    assert updated.status_code == 200

    admin = await async_client.get("/api/settings/upstream-proxy")
    assert admin.status_code == 200
    admin_payload = admin.json()
    assert admin_payload["routingEnabled"] is True
    assert admin_payload["defaultPoolId"] == pool_payload["id"]
    assert admin_payload["endpoints"][0]["id"] == endpoint_payload["id"]
    assert admin_payload["pools"][0]["endpointIds"] == [endpoint_payload["id"]]


@pytest.mark.asyncio
async def test_upstream_proxy_pool_rejects_missing_endpoint(async_client):
    response = await async_client.post(
        "/api/settings/upstream-proxy/pools",
        json={"name": "Broken Pool", "endpointIds": ["missing-endpoint"]},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "proxy_endpoint_not_found"


@pytest.mark.asyncio
async def test_upstream_proxy_pool_member_rejects_missing_endpoint(async_client):
    pool = await async_client.post(
        "/api/settings/upstream-proxy/pools",
        json={"name": "Pool A", "endpointIds": []},
    )
    assert pool.status_code == 200

    response = await async_client.post(
        f"/api/settings/upstream-proxy/pools/{pool.json()['id']}/members",
        json={"endpointId": "missing-endpoint"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "proxy_endpoint_not_found"
