from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import update

from app.core.utils.time import utcnow
from app.db.models import ApiKey, RequestLog
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeyCreatedData, ApiKeysService

pytestmark = pytest.mark.integration

_FORBIDDEN_FIELDS = {
    "accountId",
    "planType",
    "apiKeyId",
    "apiKeyName",
    "keyPrefix",
    "conversationId",
    "archiveRequestId",
    "source",
    "modelSourceId",
    "modelSourceKind",
    "useragent",
    "useragentGroup",
    "clientIp",
    "errorMessage",
    "failurePhase",
    "failureDetail",
    "failureExceptionType",
    "upstreamProxyRouteMode",
    "upstreamProxyPoolId",
    "upstreamProxyEndpointId",
    "upstreamProxyFallbackUsed",
    "upstreamProxyFailClosedReason",
}

_EXPECTED_LOG_FIELDS = {
    "requestedAt",
    "requestId",
    "requestKind",
    "model",
    "transport",
    "upstreamTransport",
    "serviceTier",
    "requestedServiceTier",
    "actualServiceTier",
    "reasoningEffort",
    "status",
    "errorCode",
    "tokens",
    "inputTokens",
    "outputTokens",
    "outputTokensRaw",
    "reasoningTokens",
    "cachedInputTokens",
    "costUsd",
    "costBreakdown",
    "latencyMs",
    "latencyFirstTokenMs",
    "latencyQueueMs",
}

_EXPECTED_PROFILE_FIELDS = {
    "name",
    "keyPrefix",
    "isActive",
    "createdAt",
    "expiresAt",
    "lastUsedAt",
    "allowedModels",
    "enforcedModel",
    "allowedReasoningEfforts",
    "enforcedReasoningEffort",
    "enforcedServiceTier",
    "trafficClass",
    "transportPolicyOverride",
}


async def _create_api_key(name: str) -> ApiKeyCreatedData:
    async with SessionLocal() as session:
        return await ApiKeysService(ApiKeysRepository(session)).create_key(
            ApiKeyCreateData(name=name, allowed_models=None, limits=[])
        )


@pytest.mark.asyncio
async def test_key_dashboard_profile_is_authenticated_and_strictly_allowlisted(
    async_client,
    db_setup,
):
    del db_setup
    expires_at = utcnow() + timedelta(days=30)
    async with SessionLocal() as session:
        created = await ApiKeysService(ApiKeysRepository(session)).create_key(
            ApiKeyCreateData(
                name="profile-dashboard-key",
                allowed_models=["gpt-5.1", "gpt-5.2"],
                allowed_reasoning_efforts=["low", "medium"],
                enforced_service_tier="priority",
                traffic_class="opportunistic",
                transport_policy_override="always_websocket",
                expires_at=expires_at,
                limits=[],
            )
        )

    missing = await async_client.get("/api/key-dashboard/profile")
    invalid = await async_client.get(
        "/api/key-dashboard/profile",
        headers={"Authorization": "Bearer sk-clb-not-a-real-key"},
    )
    response = await async_client.get(
        "/api/key-dashboard/profile",
        headers={"Authorization": f"Bearer {created.key}"},
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == _EXPECTED_PROFILE_FIELDS
    assert payload == {
        "name": "profile-dashboard-key",
        "keyPrefix": f"{created.key_prefix}…",
        "isActive": True,
        "createdAt": created.created_at.isoformat() + "Z",
        "expiresAt": expires_at.isoformat() + "Z",
        "lastUsedAt": None,
        "allowedModels": ["gpt-5.1", "gpt-5.2"],
        "enforcedModel": None,
        "allowedReasoningEfforts": ["low", "medium"],
        "enforcedReasoningEffort": None,
        "enforcedServiceTier": "priority",
        "trafficClass": "opportunistic",
        "transportPolicyOverride": "always_websocket",
    }
    serialized = response.text
    assert created.key not in serialized
    assert created.id not in serialized
    for forbidden in (
        "keyHash",
        "assignedAccountIds",
        "assignedSourceIds",
        "pooledCredits",
        "usageSections",
    ):
        assert forbidden not in payload


async def _seed_log(*, api_key_id: str, request_id: str, requested_at, deleted_at=None) -> None:
    async with SessionLocal() as session:
        session.add(
            RequestLog(
                account_id=None,
                api_key_id=api_key_id,
                request_id=request_id,
                archive_request_id=f"archive-secret-{request_id}",
                conversation_id=f"conversation-secret-{request_id}",
                request_kind="normal",
                requested_at=requested_at,
                deleted_at=deleted_at,
                model="gpt-5.1",
                plan_type="secret-plan",
                source="secret-source",
                model_source_id="secret-model-source",
                model_source_kind="openai_compatible",
                useragent="secret-useragent",
                useragent_group="secret-useragent-group",
                client_ip="192.0.2.123",
                transport="http",
                upstream_transport="websocket",
                input_tokens=100,
                output_tokens=25,
                cached_input_tokens=20,
                cost_usd=0.123,
                latency_ms=350,
                latency_first_token_ms=100,
                status="error",
                error_code="rate_limit_exceeded",
                error_message="secret-error-detail",
                failure_phase="secret-phase",
                failure_detail="secret-failure-detail",
                failure_exception_type="SecretException",
                upstream_proxy_route_mode="account_bound",
                upstream_proxy_pool_id="secret-pool",
                upstream_proxy_endpoint_id="secret-endpoint",
                upstream_proxy_fallback_used=True,
                upstream_proxy_fail_closed_reason="secret-reason",
            )
        )
        await session.commit()


def _assert_no_forbidden_fields(node: object) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            assert key not in _FORBIDDEN_FIELDS
            _assert_no_forbidden_fields(value)
    elif isinstance(node, list):
        for item in node:
            _assert_no_forbidden_fields(item)


@pytest.mark.asyncio
async def test_key_dashboard_request_logs_requires_valid_key_even_when_proxy_auth_is_optional(
    async_client,
    db_setup,
):
    del db_setup

    missing = await async_client.get("/api/key-dashboard/request-logs")
    invalid = await async_client.get(
        "/api/key-dashboard/request-logs",
        headers={"Authorization": "Bearer sk-clb-not-a-real-key"},
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401


@pytest.mark.asyncio
async def test_key_dashboard_endpoints_reject_inactive_key(async_client, db_setup):
    del db_setup
    created = await _create_api_key("inactive-dashboard-key")
    async with SessionLocal() as session:
        await session.execute(update(ApiKey).where(ApiKey.id == created.id).values(is_active=False))
        await session.commit()

    for path in ("/api/key-dashboard/profile", "/api/key-dashboard/request-logs"):
        response = await async_client.get(
            path,
            headers={"Authorization": f"Bearer {created.key}"},
        )
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_key_dashboard_endpoints_reject_expired_key(async_client, db_setup):
    del db_setup
    created = await _create_api_key("expired-dashboard-key")
    async with SessionLocal() as session:
        await session.execute(
            update(ApiKey)
            .where(ApiKey.id == created.id)
            .values(expires_at=utcnow() - timedelta(seconds=1))
        )
        await session.commit()

    for path in ("/api/key-dashboard/profile", "/api/key-dashboard/request-logs"):
        response = await async_client.get(
            path,
            headers={"Authorization": f"Bearer {created.key}"},
        )
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_key_dashboard_request_logs_are_scoped_ordered_paginated_and_redacted(
    async_client,
    db_setup,
):
    del db_setup
    own_key = await _create_api_key("own-dashboard-key")
    other_key = await _create_api_key("other-dashboard-key")
    now = utcnow()
    await _seed_log(api_key_id=own_key.id, request_id="own-older", requested_at=now - timedelta(minutes=2))
    await _seed_log(api_key_id=other_key.id, request_id="other-newest", requested_at=now)
    await _seed_log(api_key_id=own_key.id, request_id="own-newer", requested_at=now - timedelta(minutes=1))
    await _seed_log(
        api_key_id=own_key.id,
        request_id="own-deleted",
        requested_at=now + timedelta(minutes=1),
        deleted_at=now,
    )
    headers = {"Authorization": f"Bearer {own_key.key}"}

    first = await async_client.get(
        f"/api/key-dashboard/request-logs?limit=1&apiKeyId={other_key.id}",
        headers=headers,
    )
    second = await async_client.get("/api/key-dashboard/request-logs?limit=1&offset=1", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    assert first_payload["total"] == 2
    assert first_payload["hasMore"] is True
    assert [row["requestId"] for row in first_payload["requests"]] == ["own-newer"]
    assert second_payload["total"] == 2
    assert second_payload["hasMore"] is False
    assert [row["requestId"] for row in second_payload["requests"]] == ["own-older"]
    assert set(first_payload["requests"][0]) == _EXPECTED_LOG_FIELDS

    _assert_no_forbidden_fields(first_payload)
    serialized = first.text
    for secret in (
        own_key.id,
        own_key.name,
        own_key.key_prefix,
        other_key.id,
        "other-newest",
        "own-deleted",
        "secret-error-detail",
        "secret-failure-detail",
        "secret-model-source",
        "secret-pool",
        "192.0.2.123",
    ):
        assert secret not in serialized
