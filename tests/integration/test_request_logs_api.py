from __future__ import annotations

import json
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
    assert latest["apiKeyName"] == "Debug Key"
    assert latest["errorCode"] == "rate_limit_exceeded"
    assert latest["errorMessage"] == "Rate limit reached"
    assert latest["transport"] == "websocket"

    older = payload[1]
    assert older["status"] == "ok"
    assert older["apiKeyName"] is None
    assert older["tokens"] == 300
    assert older["cachedInputTokens"] is None
    assert older["transport"] == "http"


@pytest.mark.asyncio
async def test_request_log_visibility_api_returns_captured_blob(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_visibility", "visibility@example.com"))
        await logs_repo.add_log(
            account_id="acc_visibility",
            request_id="req_visibility_1",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=2,
            latency_ms=50,
            status="success",
            error_code=None,
            request_visibility=json.dumps(
                {
                    "headers": {"content-type": "application/json", "user-agent": "codex-test"},
                    "body": {"input": "hello", "apiKey": "[REDACTED]"},
                    "truncated": False,
                }
            ),
        )

    response = await async_client.get("/api/request-logs/req_visibility_1/visibility")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "requestId": "req_visibility_1",
        "captured": True,
        "unavailableReason": None,
        "truncated": False,
        "headers": {"content-type": "application/json", "user-agent": "codex-test"},
        "body": {"input": "hello", "apiKey": "[REDACTED]"},
    }
    assert "authorization" not in body["headers"]


@pytest.mark.asyncio
async def test_request_log_visibility_api_returns_not_captured_for_existing_row(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_visibility_empty", "visibility-empty@example.com"))
        await logs_repo.add_log(
            account_id="acc_visibility_empty",
            request_id="req_visibility_empty",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=0,
            latency_ms=25,
            status="success",
            error_code=None,
        )

    response = await async_client.get("/api/request-logs/req_visibility_empty/visibility")
    assert response.status_code == 200
    assert response.json() == {
        "requestId": "req_visibility_empty",
        "captured": False,
        "unavailableReason": "not_captured",
        "truncated": False,
        "headers": {},
        "body": None,
    }


@pytest.mark.asyncio
async def test_request_log_visibility_api_returns_404_for_unknown_request(async_client, db_setup):
    response = await async_client.get("/api/request-logs/req_missing/visibility")
    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Request log not found"
