from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.models import AgentProviderAccount
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_create_and_list_gemini_provider_account(async_client) -> None:
    response = await async_client.post(
        "/api/agent-providers/gemini/accounts",
        json={
            "displayName": "Gemini dev key",
            "apiKey": "AIza-test-secret",
            "projectId": "dev-project",
            "location": "global",
        },
    )

    assert response.status_code == 200
    created = response.json()
    assert created["providerId"] == "gemini"
    assert created["displayName"] == "Gemini dev key"
    assert created["authMode"] == "api_key"
    assert created["apiKeySet"] is True
    assert "apiKey" not in created

    async with SessionLocal() as session:
        stored = await session.get(AgentProviderAccount, created["accountId"])
        assert stored is not None
        assert stored.api_key_encrypted != b"AIza-test-secret"
        assert stored.credential_fingerprint == created["credentialFingerprint"]

    listed_response = await async_client.get("/api/agent-providers/gemini/accounts")

    assert listed_response.status_code == 200
    listed = listed_response.json()
    assert [account["accountId"] for account in listed["accounts"]] == [created["accountId"]]
    assert "apiKey" not in listed["accounts"][0]


@pytest.mark.asyncio
async def test_update_gemini_provider_account_rotates_key_without_returning_secret(async_client) -> None:
    response = await async_client.post(
        "/api/agent-providers/gemini/accounts",
        json={"displayName": "Gemini old", "apiKey": "old-secret"},
    )
    assert response.status_code == 200
    created = response.json()

    update_response = await async_client.patch(
        f"/api/agent-providers/gemini/accounts/{created['accountId']}",
        json={
            "displayName": "Gemini prod",
            "status": "paused",
            "apiKey": "new-secret",
            "projectId": "prod-project",
            "location": "global",
        },
    )

    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["displayName"] == "Gemini prod"
    assert updated["status"] == "paused"
    assert updated["apiKeySet"] is True
    assert updated["projectId"] == "prod-project"
    assert updated["location"] == "global"
    assert updated["credentialFingerprint"] != created["credentialFingerprint"]
    assert "apiKey" not in updated

    async with SessionLocal() as session:
        stored = await session.get(AgentProviderAccount, created["accountId"])
        assert stored is not None
        assert stored.api_key_encrypted != b"new-secret"
        assert stored.credential_fingerprint == updated["credentialFingerprint"]


@pytest.mark.asyncio
async def test_update_provider_account_clears_nullable_metadata(async_client) -> None:
    response = await async_client.post(
        "/api/agent-providers/gemini/accounts",
        json={
            "displayName": "Gemini metadata",
            "apiKey": "secret",
            "projectId": "project",
            "location": "global",
        },
    )
    assert response.status_code == 200
    created = response.json()

    update_response = await async_client.patch(
        f"/api/agent-providers/gemini/accounts/{created['accountId']}",
        json={"projectId": None, "location": None},
    )

    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["projectId"] is None
    assert updated["location"] is None
    async with SessionLocal() as session:
        stored = await session.get(AgentProviderAccount, created["accountId"])
        assert stored is not None
        assert stored.project_id is None
        assert stored.location is None


@pytest.mark.asyncio
async def test_create_and_list_antigravity_provider_account(async_client) -> None:
    response = await async_client.post(
        "/api/agent-providers/antigravity/accounts",
        json={
            "displayName": "Antigravity default",
            "externalAccountId": "default",
            "projectId": "workspace-a",
            "location": "agy",
        },
    )

    assert response.status_code == 200
    created = response.json()
    assert created["providerId"] == "antigravity"
    assert created["displayName"] == "Antigravity default"
    assert created["externalAccountId"] == "default"
    assert created["authMode"] == "cli_keyring"
    assert created["apiKeySet"] is False
    assert "apiKey" not in created

    async with SessionLocal() as session:
        stored = await session.get(AgentProviderAccount, created["accountId"])
        assert stored is not None
        assert stored.api_key_encrypted is None
        assert stored.credential_fingerprint == created["credentialFingerprint"]

    listed_response = await async_client.get("/api/agent-providers/antigravity/accounts")

    assert listed_response.status_code == 200
    listed = listed_response.json()
    assert [account["accountId"] for account in listed["accounts"]] == [created["accountId"]]


@pytest.mark.asyncio
async def test_create_antigravity_api_key_provider_account_encrypts_key(async_client) -> None:
    response = await async_client.post(
        "/api/agent-providers/antigravity/accounts",
        json={
            "displayName": "Antigravity managed",
            "authMode": "api_key",
            "apiKey": "AIza-antigravity-secret",
            "projectId": "agent-project",
        },
    )

    assert response.status_code == 200
    created = response.json()
    assert created["providerId"] == "antigravity"
    assert created["displayName"] == "Antigravity managed"
    assert created["authMode"] == "api_key"
    assert created["apiKeySet"] is True
    assert created["projectId"] == "agent-project"
    assert "apiKey" not in created

    async with SessionLocal() as session:
        stored = await session.get(AgentProviderAccount, created["accountId"])
        assert stored is not None
        assert stored.api_key_encrypted != b"AIza-antigravity-secret"
        assert stored.credential_fingerprint == created["credentialFingerprint"]


@pytest.mark.asyncio
async def test_update_antigravity_provider_account_and_duplicate_conflict(async_client) -> None:
    first_response = await async_client.post(
        "/api/agent-providers/antigravity/accounts",
        json={"displayName": "Antigravity default", "externalAccountId": "default"},
    )
    second_response = await async_client.post(
        "/api/agent-providers/antigravity/accounts",
        json={"displayName": "Antigravity other", "externalAccountId": "other"},
    )
    assert first_response.status_code == 200
    assert second_response.status_code == 200
    first = first_response.json()
    second = second_response.json()

    update_response = await async_client.patch(
        f"/api/agent-providers/antigravity/accounts/{first['accountId']}",
        json={
            "displayName": "Antigravity workspace",
            "status": "paused",
            "externalAccountId": "workspace-a",
            "location": "agy",
        },
    )

    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["displayName"] == "Antigravity workspace"
    assert updated["status"] == "paused"
    assert updated["externalAccountId"] == "workspace-a"
    assert updated["location"] == "agy"
    assert updated["credentialFingerprint"] != first["credentialFingerprint"]

    duplicate_response = await async_client.patch(
        f"/api/agent-providers/antigravity/accounts/{second['accountId']}",
        json={"externalAccountId": "workspace-a"},
    )

    assert duplicate_response.status_code == 409
    assert duplicate_response.json()["error"]["code"] == "duplicate_agent_provider_account"


@pytest.mark.asyncio
async def test_update_provider_account_missing_account_returns_404(async_client) -> None:
    response = await async_client.patch(
        "/api/agent-providers/gemini/accounts/missing",
        json={"status": "active"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "agent_provider_account_not_found"


@pytest.mark.asyncio
async def test_provider_accounts_endpoint_rejects_unknown_provider(async_client) -> None:
    response = await async_client.get("/api/agent-providers/unknown/accounts")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_agent_provider"


@pytest.mark.asyncio
async def test_provider_accounts_endpoint_requires_dashboard_auth_for_remote_clients(app_instance) -> None:
    async with app_instance.router.lifespan_context(app_instance):
        transport = ASGITransport(app=app_instance, client=("203.0.113.11", 50001))
        async with AsyncClient(transport=transport, base_url="http://lb.example") as remote_client:
            response = await remote_client.get("/api/agent-providers/gemini/accounts")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "bootstrap_required"
