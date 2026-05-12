from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, ApiKey
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository

pytestmark = pytest.mark.integration


def _make_account(account_id: str, email: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


@pytest.mark.asyncio
async def test_request_logs_api_returns_recent(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_logs", "logs@example.com"))
        session.add(
            ApiKey(
                id="key_logs_1",
                name="Debug Key",
                key_hash="hash_logs_1",
                key_prefix="sk-test",
            )
        )
        await session.commit()

        now = utcnow()
        await logs_repo.add_log(
            account_id="acc_logs",
            request_id="req_logs_1",
            model="gpt-5.1",
            input_tokens=100,
            output_tokens=200,
            latency_ms=1200,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=1),
            transport="http",
        )
        await logs_repo.add_log(
            account_id="acc_logs",
            request_id="req_logs_2",
            model="gpt-5.1",
            input_tokens=50,
            output_tokens=0,
            latency_ms=300,
            status="error",
            error_code="rate_limit_exceeded",
            error_message="Rate limit reached",
            requested_at=now,
            api_key_id="key_logs_1",
            transport="websocket",
        )

    response = await async_client.get("/api/request-logs?limit=2")
    assert response.status_code == 200
    body = response.json()
    payload = body["requests"]
    assert len(payload) == 2
    assert body["total"] == 2
    assert body["hasMore"] is False

    latest = payload[0]
    assert latest["status"] == "rate_limit"
    assert latest["apiKeyId"] == "key_logs_1"
    assert latest["apiKeyName"] == "Debug Key"
    assert latest["errorCode"] == "rate_limit_exceeded"
    assert latest["errorMessage"] == "Rate limit reached"
    assert latest["transport"] == "websocket"

    older = payload[1]
    assert older["status"] == "ok"
    assert older["apiKeyId"] is None
    assert older["apiKeyName"] is None
    assert older["tokens"] == 300
    assert older["cachedInputTokens"] is None
    assert older["transport"] == "http"


@pytest.mark.asyncio
async def test_request_logs_api_filters_platform_rows_by_routing_subject(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_logs_platform", "logs-platform@example.com"))

        now = utcnow()
        await logs_repo.add_log(
            account_id=None,
            provider_kind="openai_platform",
            routing_subject_id="plat_logs",
            request_id="req_logs_platform",
            model="gpt-5.1",
            input_tokens=12,
            output_tokens=8,
            latency_ms=180,
            status="error",
            error_code="provider_feature_unsupported",
            error_message="Unsupported route",
            requested_at=now,
            transport="http",
            route_class="openai_public_http",
            upstream_request_id="up_req_logs_platform",
            rejection_reason="platform_only_route",
        )

    response = await async_client.get("/api/request-logs", params={"accountId": "plat_logs"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["hasMore"] is False
    entry = body["requests"][0]
    assert entry["accountId"] is None
    assert entry["providerKind"] == "openai_platform"
    assert entry["routingSubjectId"] == "plat_logs"
    assert entry["upstreamRequestId"] == "up_req_logs_platform"
    assert entry["rejectionReason"] == "platform_only_route"

    options = await async_client.get("/api/request-logs/options")
    assert options.status_code == 200
    assert "plat_logs" in options.json()["accountIds"]


@pytest.mark.asyncio
async def test_request_logs_api_separates_chatgpt_and_platform_subject_filters(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_logs_chatgpt", "logs-chatgpt@example.com"))

        now = utcnow()
        await logs_repo.add_log(
            account_id="acc_logs_chatgpt",
            provider_kind="chatgpt_web",
            routing_subject_id="acc_logs_chatgpt",
            request_id="req_logs_chatgpt",
            model="gpt-5.1",
            input_tokens=24,
            output_tokens=6,
            latency_ms=210,
            status="success",
            error_code=None,
            requested_at=now - timedelta(seconds=30),
            transport="http",
            route_class="openai_public_http",
            upstream_request_id="up_req_logs_chatgpt",
        )
        await logs_repo.add_log(
            account_id=None,
            provider_kind="openai_platform",
            routing_subject_id="plat_logs_filtered",
            request_id="req_logs_platform_filtered",
            model="gpt-5.1",
            input_tokens=9,
            output_tokens=3,
            latency_ms=140,
            status="error",
            error_code="provider_feature_unsupported",
            error_message="Unsupported route",
            requested_at=now,
            transport="http",
            route_class="openai_public_http",
            upstream_request_id="up_req_logs_platform_filtered",
            rejection_reason="platform_only_route",
        )

    platform_response = await async_client.get(
        "/api/request-logs",
        params={"accountId": "plat_logs_filtered"},
    )
    assert platform_response.status_code == 200
    platform_payload = platform_response.json()
    assert platform_payload["total"] == 1
    assert platform_payload["requests"][0]["requestId"] == "req_logs_platform_filtered"
    assert platform_payload["requests"][0]["providerKind"] == "openai_platform"
    assert platform_payload["requests"][0]["routingSubjectId"] == "plat_logs_filtered"

    chatgpt_response = await async_client.get(
        "/api/request-logs",
        params={"accountId": "acc_logs_chatgpt"},
    )
    assert chatgpt_response.status_code == 200
    chatgpt_payload = chatgpt_response.json()
    assert chatgpt_payload["total"] == 1
    assert chatgpt_payload["requests"][0]["requestId"] == "req_logs_chatgpt"
    assert chatgpt_payload["requests"][0]["providerKind"] == "chatgpt_web"
    assert chatgpt_payload["requests"][0]["routingSubjectId"] == "acc_logs_chatgpt"
