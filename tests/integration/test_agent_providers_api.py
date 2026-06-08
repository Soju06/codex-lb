from __future__ import annotations

from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, RequestKind
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository

pytestmark = pytest.mark.integration


def _make_codex_account(account_id: str, email: str, status: AccountStatus = AccountStatus.ACTIVE) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=status,
        deactivation_reason=None,
    )


@pytest.mark.asyncio
async def test_agent_providers_endpoint_returns_camel_case_contract(async_client) -> None:
    response = await async_client.get("/api/agent-providers")

    assert response.status_code == 200
    body = response.json()
    assert [provider["providerId"] for provider in body["providers"]] == ["codex", "gemini", "antigravity"]
    assert body["providers"][0]["status"] == "ready"
    assert body["providers"][1]["status"] == "foundation"
    assert body["providers"][1]["capabilities"][0]["protocol"] == "gemini_api"
    assert body["providers"][1]["capabilities"][0]["operatorAction"].startswith("Add Gemini API-key")
    assert body["providers"][1]["capabilities"][2]["availableUntil"] == "2026-06-18"
    assert body["providers"][2]["status"] == "foundation"
    assert body["providers"][2]["authModes"] == ["api_key", "cli_keyring"]
    assert body["providers"][2]["capabilities"][0]["protocol"] == "interactions_api"
    assert body["providers"][2]["capabilities"][0]["status"] == "foundation"
    assert body["providers"][2]["capabilities"][0]["proxyable"] is True
    assert body["providers"][2]["capabilities"][1]["protocol"] == "antigravity_cli"
    assert "agy --print" in body["providers"][2]["capabilities"][1]["operatorAction"]


@pytest.mark.asyncio
async def test_agent_providers_endpoint_requires_dashboard_auth_for_remote_clients(app_instance) -> None:
    async with app_instance.router.lifespan_context(app_instance):
        transport = ASGITransport(app=app_instance, client=("203.0.113.11", 50001))
        async with AsyncClient(transport=transport, base_url="http://lb.example") as remote_client:
            response = await remote_client.get("/api/agent-providers")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "bootstrap_required"


@pytest.mark.asyncio
async def test_agent_provider_overview_combines_accounts_quotas_and_requests(async_client, db_setup) -> None:
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_codex_account("codex-active", "active@example.com"))
        await accounts_repo.upsert(_make_codex_account("codex-paused", "paused@example.com", AccountStatus.PAUSED))

        now = utcnow()
        await logs_repo.add_log(
            account_id="codex-active",
            request_id="req_codex_success",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=20,
            cached_input_tokens=2,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=5),
        )
        await logs_repo.add_log(
            account_id="codex-active",
            request_id="req_codex_error",
            model="gpt-5.1",
            input_tokens=1,
            output_tokens=0,
            cached_input_tokens=0,
            latency_ms=50,
            status="error",
            error_code="rate_limited",
            requested_at=now - timedelta(minutes=4),
        )
        await logs_repo.add_log(
            account_id=None,
            request_id="req_gemini_success",
            model="gemini-2.5-pro",
            input_tokens=5,
            output_tokens=7,
            cached_input_tokens=1,
            latency_ms=80,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=3),
            source="gemini",
        )
        await logs_repo.add_log(
            account_id=None,
            request_id="req_antigravity_error",
            model="agy",
            input_tokens=3,
            output_tokens=0,
            cached_input_tokens=0,
            latency_ms=120,
            status="error",
            error_code="harness_failed",
            requested_at=now - timedelta(minutes=2),
            source="antigravity",
        )
        await logs_repo.add_log(
            account_id="codex-active",
            request_id="req_warmup_excluded",
            model="gpt-5.1",
            input_tokens=99,
            output_tokens=99,
            latency_ms=1,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=1),
            request_kind=RequestKind.WARMUP.value,
        )
        await logs_repo.add_log(
            account_id=None,
            request_id="req_old_excluded",
            model="gemini-2.5-pro",
            input_tokens=99,
            output_tokens=99,
            latency_ms=1,
            status="success",
            error_code=None,
            requested_at=now - timedelta(days=2),
            source="gemini",
        )

    gemini_response = await async_client.post(
        "/api/agent-providers/gemini/accounts",
        json={"displayName": "Gemini API", "apiKey": "AIza-test-secret"},
    )
    assert gemini_response.status_code == 200
    gemini_id = gemini_response.json()["accountId"]
    antigravity_response = await async_client.post(
        "/api/agent-providers/antigravity/accounts",
        json={"displayName": "Antigravity default", "externalAccountId": "default"},
    )
    assert antigravity_response.status_code == 200
    antigravity_id = antigravity_response.json()["accountId"]

    for dimension in ("requests_per_day", "prompt_tokens"):
        response = await async_client.put(
            f"/api/agent-providers/gemini/accounts/{gemini_id}/quota-windows/{dimension}",
            json={"dimension": dimension, "used": 1, "limit": 10},
        )
        assert response.status_code == 200
    antigravity_quota = await async_client.put(
        f"/api/agent-providers/antigravity/accounts/{antigravity_id}/quota-windows/requests",
        json={"dimension": "requests", "used": 0, "limit": 5},
    )
    assert antigravity_quota.status_code == 200

    response = await async_client.get("/api/agent-providers/overview?timeframe=1d")

    assert response.status_code == 200
    body = response.json()
    providers = {provider["providerId"]: provider for provider in body["providers"]}
    assert body["timeframe"] == "1d"
    assert body["totals"] == {
        "providerCount": 3,
        "accountCount": 4,
        "activeAccountCount": 3,
        "quotaWindowCount": 3,
        "requestCount": 4,
        "successCount": 2,
        "errorCount": 2,
        "inputTokens": 19,
        "outputTokens": 27,
        "cachedInputTokens": 3,
    }
    assert providers["codex"]["accountCount"] == 2
    assert providers["codex"]["activeAccountCount"] == 1
    assert providers["codex"]["requestCount"] == 2
    assert providers["gemini"]["accountCount"] == 1
    assert providers["gemini"]["quotaWindowCount"] == 2
    assert providers["gemini"]["successCount"] == 1
    assert providers["antigravity"]["accountCount"] == 1
    assert providers["antigravity"]["quotaWindowCount"] == 1
    assert providers["antigravity"]["errorCount"] == 1


@pytest.mark.asyncio
async def test_agent_provider_overview_requires_dashboard_auth_for_remote_clients(app_instance) -> None:
    async with app_instance.router.lifespan_context(app_instance):
        transport = ASGITransport(app=app_instance, client=("203.0.113.11", 50001))
        async with AsyncClient(transport=transport, base_url="http://lb.example") as remote_client:
            response = await remote_client.get("/api/agent-providers/overview")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "bootstrap_required"
