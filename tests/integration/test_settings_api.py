from __future__ import annotations

import base64
import json
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from app.core.auth import generate_unique_account_id
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


async def _import_account(async_client, account_id: str, email: str) -> str:
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(
                {
                    "email": email,
                    "chatgpt_account_id": account_id,
                    "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
                }
            ),
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": account_id,
        },
    }
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    return generate_unique_account_id(account_id, email)


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
    assert payload["warmupModel"] == "gpt-5.4-mini"
    assert payload["importWithoutOverwrite"] is True
    assert payload["totpRequiredOnLogin"] is False
    assert payload["totpConfigured"] is False
    assert payload["apiKeyAuthEnabled"] is False
    assert payload["hideUpstreamQuotaFromApiKeys"] is False
    assert payload["limitWarmupEnabled"] is False
    assert payload["limitWarmupWindows"] == "both"
    assert payload["limitWarmupModel"] == "auto"
    assert payload["limitWarmupPrompt"] == "Say OK."
    assert payload["limitWarmupCooldownSeconds"] == 3600
    assert payload["limitWarmupExhaustedThresholdPercent"] == 99.0
    assert payload["limitWarmupMinAvailablePercent"] == 100.0
    assert payload["weeklyPaceWorkingDays"] == "0,1,2,3,4,5,6"
    assert payload["weeklyPaceSmoothingMinutes"] == 30
    assert payload["limitWarmupStaggeredIdleEnabled"] is False

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
            "preferEarlierResetWindow": "secondary",
            "singleAccountId": None,
            "openaiCacheAffinityMaxAgeSeconds": 180,
            "dashboardSessionTtlSeconds": 31536000,
            "httpResponsesSessionBridgePromptCacheIdleTtlSeconds": 1800,
            "httpResponsesSessionBridgeGatewaySafeMode": True,
            "stickyReallocationBudgetThresholdPct": 85.0,
            "stickyReallocationPrimaryBudgetThresholdPct": 85.0,
            "stickyReallocationSecondaryBudgetThresholdPct": 98.0,
            "warmupModel": "gpt-5.4-nano",
            "importWithoutOverwrite": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
            "hideUpstreamQuotaFromApiKeys": True,
            "limitWarmupEnabled": True,
            "limitWarmupWindows": "primary",
            "limitWarmupModel": "gpt-5.1-codex-mini",
            "limitWarmupPrompt": "Say OK.",
            "limitWarmupCooldownSeconds": 7200,
            "limitWarmupExhaustedThresholdPercent": 98.5,
            "limitWarmupMinAvailablePercent": 99.0,
            "weeklyPaceWorkingDays": "0,1,2,3,4",
            "weeklyPaceSmoothingMinutes": 120,
            "limitWarmupStaggeredIdleEnabled": True,
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
    assert updated["preferEarlierResetWindow"] == "secondary"
    assert updated["singleAccountId"] is None
    assert updated["openaiCacheAffinityMaxAgeSeconds"] == 180
    assert updated["dashboardSessionTtlSeconds"] == 31536000
    assert updated["httpResponsesSessionBridgePromptCacheIdleTtlSeconds"] == 1800
    assert updated["httpResponsesSessionBridgeGatewaySafeMode"] is True
    assert updated["stickyReallocationBudgetThresholdPct"] == 85.0
    assert updated["stickyReallocationPrimaryBudgetThresholdPct"] == 85.0
    assert updated["stickyReallocationSecondaryBudgetThresholdPct"] == 98.0
    assert updated["warmupModel"] == "gpt-5.4-nano"
    assert updated["importWithoutOverwrite"] is False
    assert updated["totpRequiredOnLogin"] is False
    assert updated["totpConfigured"] is False
    assert updated["apiKeyAuthEnabled"] is True
    assert updated["hideUpstreamQuotaFromApiKeys"] is True
    assert updated["limitWarmupEnabled"] is True
    assert updated["limitWarmupWindows"] == "primary"
    assert updated["limitWarmupModel"] == "gpt-5.1-codex-mini"
    assert updated["limitWarmupPrompt"] == "Say OK."
    assert updated["limitWarmupCooldownSeconds"] == 7200
    assert updated["limitWarmupExhaustedThresholdPercent"] == 98.5
    assert updated["limitWarmupMinAvailablePercent"] == 99.0
    assert updated["weeklyPaceWorkingDays"] == "0,1,2,3,4"
    assert updated["weeklyPaceSmoothingMinutes"] == 120
    assert updated["limitWarmupStaggeredIdleEnabled"] is True

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
    assert payload["preferEarlierResetWindow"] == "secondary"
    assert payload["singleAccountId"] is None
    assert payload["openaiCacheAffinityMaxAgeSeconds"] == 180
    assert payload["dashboardSessionTtlSeconds"] == 31536000
    assert payload["httpResponsesSessionBridgePromptCacheIdleTtlSeconds"] == 1800
    assert payload["httpResponsesSessionBridgeGatewaySafeMode"] is True
    assert payload["stickyReallocationBudgetThresholdPct"] == 85.0
    assert payload["stickyReallocationPrimaryBudgetThresholdPct"] == 85.0
    assert payload["stickyReallocationSecondaryBudgetThresholdPct"] == 98.0
    assert payload["warmupModel"] == "gpt-5.4-nano"
    assert payload["importWithoutOverwrite"] is False
    assert payload["totpRequiredOnLogin"] is False
    assert payload["totpConfigured"] is False
    assert payload["apiKeyAuthEnabled"] is True
    assert payload["hideUpstreamQuotaFromApiKeys"] is True
    assert payload["limitWarmupEnabled"] is True
    assert payload["limitWarmupWindows"] == "primary"
    assert payload["limitWarmupModel"] == "gpt-5.1-codex-mini"
    assert payload["limitWarmupPrompt"] == "Say OK."
    assert payload["limitWarmupCooldownSeconds"] == 7200
    assert payload["limitWarmupExhaustedThresholdPercent"] == 98.5
    assert payload["limitWarmupMinAvailablePercent"] == 99.0
    assert payload["weeklyPaceWorkingDays"] == "0,1,2,3,4"
    assert payload["weeklyPaceSmoothingMinutes"] == 120


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
async def test_settings_api_returns_known_additional_quota_policies(async_client):
    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()

    assert payload["additionalQuotaRoutingPolicies"] == {}
    assert payload["additionalQuotaPolicies"] == [
        {
            "quotaKey": "codex_spark",
            "displayLabel": "GPT-5.3-Codex-Spark",
            "routingPolicy": "burn_first",
            "modelIds": ["gpt_5_3_codex_spark"],
        }
    ]

    update_payload = {
        "stickyThreadsEnabled": payload["stickyThreadsEnabled"],
        "preferEarlierResetAccounts": payload["preferEarlierResetAccounts"],
        "additionalQuotaRoutingPolicies": {"codex_spark": "preserve"},
    }
    response = await async_client.put("/api/settings", json=update_payload)
    assert response.status_code == 200
    updated = response.json()
    assert updated["additionalQuotaRoutingPolicies"] == {"codex_spark": "preserve"}
    assert updated["additionalQuotaPolicies"][0]["routingPolicy"] == "preserve"

    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    persisted = response.json()
    assert persisted["additionalQuotaRoutingPolicies"] == {"codex_spark": "preserve"}
    assert persisted["additionalQuotaPolicies"][0]["routingPolicy"] == "preserve"


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
    assert updated["hideUpstreamQuotaFromApiKeys"] == original["hideUpstreamQuotaFromApiKeys"]


@pytest.mark.asyncio
async def test_settings_api_rejects_invalid_weekly_pace_working_days(async_client):
    response = await async_client.put(
        "/api/settings",
        json={"weeklyPaceWorkingDays": "0,1,7"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_settings_api_rejects_invalid_weekly_pace_smoothing_minutes(async_client):
    response = await async_client.put(
        "/api/settings",
        json={"weeklyPaceSmoothingMinutes": 45},
    )

    assert response.status_code == 422


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
async def test_upstream_proxy_endpoint_test_probes_configured_proxy(async_client, monkeypatch):
    captured: dict[str, object] = {}

    class _Response:
        status_code = 204

    class _FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url):
            captured["url"] = url
            return _Response()

    monkeypatch.setattr("app.modules.settings.api.httpx.AsyncClient", _FakeAsyncClient)

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

    response = await async_client.post(
        f"/api/settings/upstream-proxy/endpoints/{endpoint.json()['id']}/test",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["statusCode"] == 204
    assert payload["error"] is None
    client_kwargs = cast(dict[str, Any], captured["client_kwargs"])
    assert captured["url"] == "https://chatgpt.com/cdn-cgi/trace"
    assert client_kwargs["proxy"] == "http://user:secret@proxy.internal:8080"
    assert "secret" not in str(payload)


@pytest.mark.asyncio
async def test_upstream_proxy_endpoint_test_rejects_proxy_auth_response(async_client, monkeypatch):
    class _Response:
        status_code = 407

    class _FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url):
            return _Response()

    monkeypatch.setattr("app.modules.settings.api.httpx.AsyncClient", _FakeAsyncClient)

    endpoint = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={
            "name": "Proxy Auth",
            "scheme": "http",
            "host": "proxy.internal",
            "port": 8080,
            "username": "user",
            "password": "wrong",
        },
    )
    assert endpoint.status_code == 200

    response = await async_client.post(
        f"/api/settings/upstream-proxy/endpoints/{endpoint.json()['id']}/test",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["statusCode"] == 407
    assert payload["error"] == "proxy_auth_failed"
    assert "wrong" not in str(payload)


@pytest.mark.asyncio
async def test_upstream_proxy_endpoint_test_probes_socks_proxy(async_client, monkeypatch):
    captured: dict[str, object] = {}

    class _Response:
        status = 204

    class _FakeConnector:
        def __init__(self, **kwargs):
            captured["connector_kwargs"] = kwargs

    class _FakeAiohttpSession:
        def __init__(self, **kwargs):
            captured["session_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, **kwargs):
            captured["url"] = url
            captured["get_kwargs"] = kwargs
            return _Response()

    monkeypatch.setattr("app.modules.settings.api.ProxyConnector", _FakeConnector)
    monkeypatch.setattr("app.modules.settings.api.aiohttp.ClientSession", _FakeAiohttpSession)

    endpoint = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={
            "name": "Proxy A",
            "scheme": "socks5",
            "host": "proxy.internal",
            "port": 1080,
        },
    )
    assert endpoint.status_code == 200

    response = await async_client.post(
        f"/api/settings/upstream-proxy/endpoints/{endpoint.json()['id']}/test",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["statusCode"] == 204
    assert payload["error"] is None
    connector_kwargs = cast(dict[str, Any], captured["connector_kwargs"])
    session_kwargs = cast(dict[str, Any], captured["session_kwargs"])
    assert captured["url"] == "https://chatgpt.com/cdn-cgi/trace"
    assert cast(dict[str, Any], captured["get_kwargs"])["allow_redirects"] is False
    assert connector_kwargs["host"] == "proxy.internal"
    assert connector_kwargs["port"] == 1080
    assert connector_kwargs["rdns"] is True
    assert session_kwargs["trust_env"] is False


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


@pytest.mark.asyncio
async def test_upstream_proxy_pool_member_rejects_duplicate_endpoint(async_client):
    endpoint = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={"name": "Proxy A", "scheme": "http", "host": "proxy.internal", "port": 8080},
    )
    assert endpoint.status_code == 200
    endpoint_id = endpoint.json()["id"]
    pool = await async_client.post(
        "/api/settings/upstream-proxy/pools",
        json={"name": "Pool A", "endpointIds": [endpoint_id]},
    )
    assert pool.status_code == 200

    response = await async_client.post(
        f"/api/settings/upstream-proxy/pools/{pool.json()['id']}/members",
        json={"endpointId": endpoint_id},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "proxy_pool_member_duplicate"


@pytest.mark.asyncio
async def test_settings_update_rejects_missing_default_proxy_pool(async_client):
    settings = await async_client.get("/api/settings")
    body = settings.json()
    body["upstreamProxyDefaultPoolId"] = "missing-pool"

    response = await async_client.put("/api/settings", json=body)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "proxy_pool_not_found"


@pytest.mark.asyncio
async def test_account_proxy_binding_rejects_missing_targets(async_client):
    missing_account = await async_client.put(
        "/api/settings/upstream-proxy/accounts/missing-account/binding",
        json={"poolId": "missing-pool", "isActive": True},
    )
    assert missing_account.status_code == 400
    assert missing_account.json()["error"]["code"] == "account_not_found"

    account_id = await _import_account(async_client, "acc-settings-proxy-binding", "settings-proxy@example.com")
    missing_pool = await async_client.put(
        f"/api/settings/upstream-proxy/accounts/{account_id}/binding",
        json={"poolId": "missing-pool", "isActive": True},
    )
    assert missing_pool.status_code == 400
    assert missing_pool.json()["error"]["code"] == "proxy_pool_not_found"


@pytest.mark.asyncio
async def test_account_proxy_binding_reactivates_proxy_unreachable_account(async_client):
    from app.modules.proxy.account_cache import (
        get_account_selection_cache,
        is_account_routing_unavailable,
        mark_account_routing_unavailable,
    )

    cache_generation = get_account_selection_cache().generation
    account_id = await _import_account(async_client, "acc-settings-proxy-repair", "settings-proxy-repair@example.com")
    mark_account_routing_unavailable(account_id)
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        account.status = AccountStatus.DEACTIVATED
        account.deactivation_reason = "proxy_unreachable: ProxyConnectionError - connection refused"
        await session.commit()

    endpoint = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={"name": "repair proxy", "scheme": "http", "host": "proxy.test", "port": 8080},
    )
    assert endpoint.status_code == 200
    pool = await async_client.post(
        "/api/settings/upstream-proxy/pools",
        json={"name": "repair pool", "endpointIds": [endpoint.json()["id"]]},
    )
    assert pool.status_code == 200
    binding = await async_client.put(
        f"/api/settings/upstream-proxy/accounts/{account_id}/binding",
        json={"poolId": pool.json()["id"], "isActive": True},
    )

    assert binding.status_code == 200
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        assert account.status == AccountStatus.ACTIVE
        assert account.deactivation_reason is None
    assert get_account_selection_cache().generation > cache_generation
    assert is_account_routing_unavailable(account_id) is False


@pytest.mark.asyncio
async def test_account_proxy_binding_closes_existing_bridge_sessions(async_client, monkeypatch):
    close_sessions = AsyncMock()
    monkeypatch.setattr(
        "app.modules.settings.api.get_proxy_service_for_app",
        lambda _app: type("_ProxyService", (), {"close_http_bridge_sessions_for_account": close_sessions})(),
    )
    account_id = await _import_account(async_client, "acc-settings-proxy-close", "settings-proxy-close@example.com")
    endpoint = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={"name": "close proxy", "scheme": "http", "host": "proxy.test", "port": 8080},
    )
    assert endpoint.status_code == 200
    pool = await async_client.post(
        "/api/settings/upstream-proxy/pools",
        json={"name": "close pool", "endpointIds": [endpoint.json()["id"]]},
    )
    assert pool.status_code == 200

    binding = await async_client.put(
        f"/api/settings/upstream-proxy/accounts/{account_id}/binding",
        json={"poolId": pool.json()["id"], "isActive": True},
    )

    assert binding.status_code == 200
    close_sessions.assert_awaited_once_with(account_id)


@pytest.mark.asyncio
async def test_account_proxy_binding_disable_closes_existing_bridge_sessions(async_client, monkeypatch):
    close_sessions = AsyncMock()
    monkeypatch.setattr(
        "app.modules.settings.api.get_proxy_service_for_app",
        lambda _app: type("_ProxyService", (), {"close_http_bridge_sessions_for_account": close_sessions})(),
    )
    account_id = await _import_account(
        async_client,
        "acc-settings-proxy-disable-close",
        "settings-proxy-disable-close@example.com",
    )
    endpoint = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={"name": "disable close proxy", "scheme": "http", "host": "proxy.test", "port": 8080},
    )
    assert endpoint.status_code == 200
    pool = await async_client.post(
        "/api/settings/upstream-proxy/pools",
        json={"name": "disable close pool", "endpointIds": [endpoint.json()["id"]]},
    )
    assert pool.status_code == 200
    enabled = await async_client.put(
        f"/api/settings/upstream-proxy/accounts/{account_id}/binding",
        json={"poolId": pool.json()["id"], "isActive": True},
    )
    assert enabled.status_code == 200
    close_sessions.reset_mock()

    disabled = await async_client.put(
        f"/api/settings/upstream-proxy/accounts/{account_id}/binding",
        json={"poolId": pool.json()["id"], "isActive": False},
    )

    assert disabled.status_code == 200
    close_sessions.assert_awaited_once_with(account_id)


@pytest.mark.asyncio
async def test_account_proxy_binding_rebind_active_account_closes_bridge_sessions(async_client, monkeypatch):
    close_sessions = AsyncMock()
    monkeypatch.setattr(
        "app.modules.settings.api.get_proxy_service_for_app",
        lambda _app: type("_ProxyService", (), {"close_http_bridge_sessions_for_account": close_sessions})(),
    )

    account_id = await _import_account(
        async_client,
        "acc-settings-proxy-rebind-close",
        "settings-proxy-rebind-close@example.com",
    )

    endpoint_one = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={"name": "rebind close proxy one", "scheme": "http", "host": "proxy.test", "port": 8080},
    )
    assert endpoint_one.status_code == 200
    pool_one = await async_client.post(
        "/api/settings/upstream-proxy/pools",
        json={"name": "rebind close pool one", "endpointIds": [endpoint_one.json()["id"]]},
    )
    assert pool_one.status_code == 200

    endpoint_two = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={"name": "rebind close proxy two", "scheme": "http", "host": "proxy-2.test", "port": 8080},
    )
    assert endpoint_two.status_code == 200
    pool_two = await async_client.post(
        "/api/settings/upstream-proxy/pools",
        json={"name": "rebind close pool two", "endpointIds": [endpoint_two.json()["id"]]},
    )
    assert pool_two.status_code == 200

    first_binding = await async_client.put(
        f"/api/settings/upstream-proxy/accounts/{account_id}/binding",
        json={"poolId": pool_one.json()["id"], "isActive": True},
    )
    assert first_binding.status_code == 200
    close_sessions.assert_awaited_once_with(account_id)
    close_sessions.reset_mock()

    rebinding = await async_client.put(
        f"/api/settings/upstream-proxy/accounts/{account_id}/binding",
        json={"poolId": pool_two.json()["id"], "isActive": True},
    )
    assert rebinding.status_code == 200
    close_sessions.assert_awaited_once_with(account_id)


@pytest.mark.asyncio
async def test_account_proxy_binding_does_not_reactivate_session_deactivated_account(async_client):
    account_id = await _import_account(async_client, "acc-settings-proxy-reauth", "settings-proxy-reauth@example.com")
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        account.status = AccountStatus.DEACTIVATED
        account.deactivation_reason = "ChatGPT session ended - re-login required"
        await session.commit()

    endpoint = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={"name": "reauth proxy", "scheme": "http", "host": "proxy.test", "port": 8080},
    )
    assert endpoint.status_code == 200
    pool = await async_client.post(
        "/api/settings/upstream-proxy/pools",
        json={"name": "reauth pool", "endpointIds": [endpoint.json()["id"]]},
    )
    assert pool.status_code == 200
    binding = await async_client.put(
        f"/api/settings/upstream-proxy/accounts/{account_id}/binding",
        json={"poolId": pool.json()["id"], "isActive": True},
    )

    assert binding.status_code == 200
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        assert account.status == AccountStatus.DEACTIVATED
        assert account.deactivation_reason == "ChatGPT session ended - re-login required"
