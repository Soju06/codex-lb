from __future__ import annotations

from datetime import timedelta
from hashlib import sha256

import pytest

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, ApiKey
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository

pytestmark = pytest.mark.integration


def _hash_session_id(value: str) -> str:
    return f"sha256:{sha256(value.encode('utf-8')).hexdigest()[:12]}"


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
            request_kind="responses",
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
            request_kind="compact",
            session_id_hash=_hash_session_id("sid-logs-2"),
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
    assert latest["requestKind"] == "compact"
    assert latest["sessionIdHash"] == _hash_session_id("sid-logs-2")

    older = payload[1]
    assert older["status"] == "ok"
    assert older["apiKeyName"] is None
    assert older["tokens"] == 300
    assert older["cachedInputTokens"] is None
    assert older["transport"] == "http"
    assert older["requestKind"] == "responses"
    assert older["sessionIdHash"] is None


@pytest.mark.asyncio
async def test_request_logs_api_filters_by_request_kind_and_transport(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_filter_logs", "filters@example.com"))

        now = utcnow()
        await logs_repo.add_log(
            account_id="acc_filter_logs",
            request_id="req_filter_http",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=10,
            latency_ms=120,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=2),
            transport="http",
            request_kind="responses",
        )
        await logs_repo.add_log(
            account_id="acc_filter_logs",
            request_id="req_filter_ws",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=10,
            latency_ms=140,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=1),
            transport="websocket",
            request_kind="compact",
        )
        await logs_repo.add_log(
            account_id="acc_filter_logs",
            request_id="req_filter_compact_http",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=10,
            latency_ms=160,
            status="success",
            error_code=None,
            requested_at=now,
            transport="http",
            request_kind="compact",
        )

    response = await async_client.get("/api/request-logs?requestKind=compact&transport=websocket")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["hasMore"] is False
    assert [entry["requestId"] for entry in body["requests"]] == ["req_filter_ws"]
