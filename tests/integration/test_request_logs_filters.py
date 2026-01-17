from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
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


def _cost(input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float:
    billable = input_tokens - cached_tokens
    return (billable / 1_000_000) * 1.25 + (cached_tokens / 1_000_000) * 0.125 + (output_tokens / 1_000_000) * 10.0


@pytest.mark.asyncio
async def test_request_logs_status_ok_filters_success(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_ok", "ok@example.com"))

        await logs_repo.add_log(
            account_id="acc_ok",
            request_id="req_ok_1",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=20,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=2),
        )
        await logs_repo.add_log(
            account_id="acc_ok",
            request_id="req_ok_2",
            model="gpt-5.1",
            input_tokens=5,
            output_tokens=0,
            latency_ms=50,
            status="error",
            error_code="rate_limit_exceeded",
            requested_at=now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/request-logs?status=ok")
    assert response.status_code == 200
    payload = response.json()["requests"]
    assert len(payload) == 1
    assert payload[0]["status"] == "ok"
    assert payload[0]["errorCode"] is None


@pytest.mark.asyncio
async def test_request_logs_status_rate_limit_filters_codes(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_rate", "rate@example.com"))

        await logs_repo.add_log(
            account_id="acc_rate",
            request_id="req_rate_1",
            model="gpt-5.1",
            input_tokens=1,
            output_tokens=1,
            latency_ms=10,
            status="error",
            error_code="rate_limit_exceeded",
            requested_at=now - timedelta(minutes=1),
        )
        await logs_repo.add_log(
            account_id="acc_rate",
            request_id="req_rate_2",
            model="gpt-5.1",
            input_tokens=1,
            output_tokens=1,
            latency_ms=10,
            status="error",
            error_code="insufficient_quota",
            requested_at=now - timedelta(minutes=2),
        )

    response = await async_client.get("/api/request-logs?status=rate_limit")
    assert response.status_code == 200
    payload = response.json()["requests"]
    assert len(payload) == 1
    assert payload[0]["status"] == "rate_limit"
    assert payload[0]["errorCode"] == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_request_logs_status_quota_filters_codes(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_quota", "quota@example.com"))

        await logs_repo.add_log(
            account_id="acc_quota",
            request_id="req_quota_1",
            model="gpt-5.1",
            input_tokens=1,
            output_tokens=1,
            latency_ms=10,
            status="error",
            error_code="insufficient_quota",
            requested_at=now - timedelta(minutes=3),
        )
        await logs_repo.add_log(
            account_id="acc_quota",
            request_id="req_quota_2",
            model="gpt-5.1",
            input_tokens=1,
            output_tokens=1,
            latency_ms=10,
            status="error",
            error_code="usage_not_included",
            requested_at=now - timedelta(minutes=2),
        )
        await logs_repo.add_log(
            account_id="acc_quota",
            request_id="req_quota_3",
            model="gpt-5.1",
            input_tokens=1,
            output_tokens=1,
            latency_ms=10,
            status="error",
            error_code="quota_exceeded",
            requested_at=now - timedelta(minutes=1),
        )
        await logs_repo.add_log(
            account_id="acc_quota",
            request_id="req_quota_4",
            model="gpt-5.1",
            input_tokens=1,
            output_tokens=1,
            latency_ms=10,
            status="error",
            error_code="rate_limit_exceeded",
            requested_at=now - timedelta(minutes=4),
        )

    response = await async_client.get("/api/request-logs?status=quota&limit=10")
    assert response.status_code == 200
    payload = response.json()["requests"]
    codes = {entry["errorCode"] for entry in payload}
    assert codes == {"insufficient_quota", "usage_not_included", "quota_exceeded"}
    assert all(entry["status"] == "quota" for entry in payload)


@pytest.mark.asyncio
async def test_request_logs_filters_by_account_model_and_time(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_filter", "filter@example.com"))

        await logs_repo.add_log(
            account_id="acc_filter",
            request_id="req_filter_1",
            model="gpt-5.1",
            input_tokens=1,
            output_tokens=1,
            latency_ms=10,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=20),
        )
        await logs_repo.add_log(
            account_id="acc_filter",
            request_id="req_filter_2",
            model="gpt-5.1",
            input_tokens=2,
            output_tokens=2,
            latency_ms=10,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=10),
        )
        await logs_repo.add_log(
            account_id="acc_filter",
            request_id="req_filter_3",
            model="gpt-5.2",
            input_tokens=3,
            output_tokens=3,
            latency_ms=10,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=5),
        )

    since = (now - timedelta(minutes=15)).isoformat()
    until = (now - timedelta(minutes=7)).isoformat()
    response = await async_client.get(
        f"/api/request-logs?accountId=acc_filter&model=gpt-5.1&since={since}&until={until}"
    )
    assert response.status_code == 200
    payload = response.json()["requests"]
    assert len(payload) == 1
    assert payload[0]["model"] == "gpt-5.1"
    assert payload[0]["tokens"] == 4


@pytest.mark.asyncio
async def test_request_logs_tokens_and_cost_use_reasoning_tokens(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_reason", "reason@example.com"))

        await logs_repo.add_log(
            account_id="acc_reason",
            request_id="req_reason_1",
            model="gpt-5.1",
            input_tokens=1000,
            output_tokens=None,
            cached_input_tokens=100,
            reasoning_tokens=400,
            latency_ms=50,
            status="success",
            error_code=None,
            requested_at=now,
        )

    response = await async_client.get("/api/request-logs?accountId=acc_reason&limit=1")
    assert response.status_code == 200
    payload = response.json()["requests"]
    assert len(payload) == 1
    entry = payload[0]
    assert entry["tokens"] == 1400
    assert entry["cachedInputTokens"] == 100
    expected = round(_cost(1000, 400, 100), 6)
    assert entry["costUsd"] == pytest.approx(expected)
