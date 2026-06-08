from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


async def _create_gemini_account(async_client, name: str) -> str:
    response = await async_client.post(
        "/api/agent-providers/gemini/accounts",
        json={"displayName": name, "apiKey": f"AIza-{name}-secret"},
    )
    assert response.status_code == 200
    return response.json()["accountId"]


@pytest.mark.asyncio
async def test_provider_routing_settings_are_persisted_before_preflight(async_client) -> None:
    account_id = await _create_gemini_account(async_client, "Gemini single")

    settings_response = await async_client.patch(
        "/api/agent-providers/gemini/routing/settings",
        json={
            "strategy": "single_account",
            "singleAccountId": account_id,
            "quotaThresholdPct": 95.0,
        },
    )

    assert settings_response.status_code == 200
    assert settings_response.json()["strategy"] == "single_account"

    preflight_response = await async_client.post("/api/agent-providers/gemini/routing/preflight")

    assert preflight_response.status_code == 200
    preflight = preflight_response.json()
    assert preflight["settings"]["strategy"] == "single_account"
    assert preflight["selectedAccountId"] == account_id
    assert preflight["candidateAccountIds"] == [account_id]


@pytest.mark.asyncio
async def test_single_account_partial_settings_update_preserves_account(async_client) -> None:
    account_id = await _create_gemini_account(async_client, "Gemini partial settings")
    settings_response = await async_client.patch(
        "/api/agent-providers/gemini/routing/settings",
        json={"strategy": "single_account", "singleAccountId": account_id},
    )
    assert settings_response.status_code == 200

    update_response = await async_client.patch(
        "/api/agent-providers/gemini/routing/settings",
        json={"quotaThresholdPct": 80.0},
    )

    assert update_response.status_code == 200
    payload = update_response.json()
    assert payload["strategy"] == "single_account"
    assert payload["singleAccountId"] == account_id
    assert payload["quotaThresholdPct"] == 80.0
    preflight_response = await async_client.post("/api/agent-providers/gemini/routing/preflight")
    assert preflight_response.status_code == 200
    assert preflight_response.json()["selectedAccountId"] == account_id


@pytest.mark.asyncio
async def test_provider_preflight_honors_quota_budget_before_reset_drain(async_client) -> None:
    blocked_id = await _create_gemini_account(async_client, "Gemini blocked")
    safe_id = await _create_gemini_account(async_client, "Gemini safe")
    now = datetime.now(timezone.utc)

    blocked_quota = await async_client.put(
        f"/api/agent-providers/gemini/accounts/{blocked_id}/quota-windows/requests_per_day",
        json={
            "dimension": "requests_per_day",
            "used": 99,
            "limit": 100,
            "resetAt": (now + timedelta(minutes=5)).isoformat(),
        },
    )
    safe_quota = await async_client.put(
        f"/api/agent-providers/gemini/accounts/{safe_id}/quota-windows/requests_per_day",
        json={
            "dimension": "requests_per_day",
            "used": 20,
            "limit": 100,
            "resetAt": (now + timedelta(days=2)).isoformat(),
        },
    )
    assert blocked_quota.status_code == 200
    assert safe_quota.status_code == 200

    settings_response = await async_client.patch(
        "/api/agent-providers/gemini/routing/settings",
        json={"strategy": "reset_drain", "quotaThresholdPct": 95.0},
    )
    assert settings_response.status_code == 200

    preflight_response = await async_client.post("/api/agent-providers/gemini/routing/preflight")

    assert preflight_response.status_code == 200
    preflight = preflight_response.json()
    assert preflight["selectedAccountId"] == safe_id
    assert preflight["candidateAccountIds"] == [safe_id]
    assert {account["accountId"] for account in preflight["accounts"]} == {blocked_id, safe_id}


@pytest.mark.asyncio
async def test_provider_ordered_fallback_settings_drive_preflight(async_client) -> None:
    blocked_id = await _create_gemini_account(async_client, "Gemini ordered blocked")
    safe_id = await _create_gemini_account(async_client, "Gemini ordered safe")
    now = datetime.now(timezone.utc)

    blocked_quota = await async_client.put(
        f"/api/agent-providers/gemini/accounts/{blocked_id}/quota-windows/requests_per_day",
        json={
            "dimension": "requests_per_day",
            "used": 99,
            "limit": 100,
            "resetAt": (now + timedelta(days=1)).isoformat(),
        },
    )
    safe_quota = await async_client.put(
        f"/api/agent-providers/gemini/accounts/{safe_id}/quota-windows/requests_per_day",
        json={
            "dimension": "requests_per_day",
            "used": 10,
            "limit": 100,
            "resetAt": (now + timedelta(days=1)).isoformat(),
        },
    )
    assert blocked_quota.status_code == 200
    assert safe_quota.status_code == 200

    settings_response = await async_client.patch(
        "/api/agent-providers/gemini/routing/settings",
        json={
            "strategy": "ordered_fallback",
            "orderedAccountIds": [blocked_id, safe_id, blocked_id],
            "quotaThresholdPct": 95.0,
        },
    )
    assert settings_response.status_code == 200
    assert settings_response.json()["orderedAccountIds"] == [blocked_id, safe_id]

    preflight_response = await async_client.post("/api/agent-providers/gemini/routing/preflight")

    assert preflight_response.status_code == 200
    preflight = preflight_response.json()
    assert preflight["settings"]["strategy"] == "ordered_fallback"
    assert preflight["selectedAccountId"] == safe_id
    assert preflight["candidateAccountIds"] == [safe_id]


@pytest.mark.asyncio
async def test_provider_preflight_excludes_paused_accounts(async_client) -> None:
    paused_id = await _create_gemini_account(async_client, "Gemini paused")
    active_id = await _create_gemini_account(async_client, "Gemini active")

    pause_response = await async_client.patch(
        f"/api/agent-providers/gemini/accounts/{paused_id}",
        json={"status": "paused"},
    )
    assert pause_response.status_code == 200

    settings_response = await async_client.patch(
        "/api/agent-providers/gemini/routing/settings",
        json={
            "strategy": "ordered_fallback",
            "orderedAccountIds": [paused_id, active_id],
            "quotaThresholdPct": 95.0,
        },
    )
    assert settings_response.status_code == 200

    preflight_response = await async_client.post("/api/agent-providers/gemini/routing/preflight")

    assert preflight_response.status_code == 200
    preflight = preflight_response.json()
    assert preflight["selectedAccountId"] == active_id
    assert preflight["candidateAccountIds"] == [active_id]


@pytest.mark.asyncio
async def test_provider_ordered_fallback_rejects_missing_order(async_client) -> None:
    response = await async_client.patch(
        "/api/agent-providers/gemini/routing/settings",
        json={"strategy": "ordered_fallback", "orderedAccountIds": []},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_agent_provider_routing"


@pytest.mark.asyncio
async def test_provider_routing_endpoint_requires_dashboard_auth_for_remote_clients(app_instance) -> None:
    async with app_instance.router.lifespan_context(app_instance):
        transport = ASGITransport(app=app_instance, client=("203.0.113.21", 50001))
        async with AsyncClient(transport=transport, base_url="http://lb.example") as remote_client:
            response = await remote_client.post("/api/agent-providers/gemini/routing/preflight")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "bootstrap_required"
