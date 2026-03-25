from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import update

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


def _cost(
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    *,
    input_rate: float = 1.25,
    cached_rate: float = 0.125,
    output_rate: float = 10.0,
) -> float:
    billable = input_tokens - cached_tokens
    return (
        (billable / 1_000_000) * input_rate
        + (cached_tokens / 1_000_000) * cached_rate
        + (output_tokens / 1_000_000) * output_rate
    )


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
async def test_request_logs_expose_requested_and_actual_service_tiers(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_tier", "tiers@example.com"))

        await logs_repo.add_log(
            account_id="acc_tier",
            request_id="req_tier_1",
            model="gpt-5.4",
            input_tokens=1,
            output_tokens=1,
            latency_ms=10,
            status="success",
            error_code=None,
            service_tier="default",
            requested_service_tier="priority",
            actual_service_tier="default",
            requested_at=now,
        )

    response = await async_client.get("/api/request-logs")
    assert response.status_code == 200
    payload = response.json()["requests"]
    assert payload[0]["serviceTier"] == "default"
    assert payload[0]["requestedServiceTier"] == "priority"
    assert payload[0]["actualServiceTier"] == "default"


@pytest.mark.asyncio
async def test_request_logs_filters_by_multiple_accounts_returns_union(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_multi_a", "a@example.com"))
        await accounts_repo.upsert(_make_account("acc_multi_b", "b@example.com"))

        await logs_repo.add_log(
            account_id="acc_multi_a",
            request_id="req_multi_1",
            model="gpt-5.1",
            input_tokens=1,
            output_tokens=1,
            latency_ms=10,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=1),
        )
        await logs_repo.add_log(
            account_id="acc_multi_b",
            request_id="req_multi_2",
            model="gpt-5.1",
            input_tokens=1,
            output_tokens=1,
            latency_ms=10,
            status="success",
            error_code=None,
            requested_at=now,
        )

    response = await async_client.get("/api/request-logs?accountId=acc_multi_a&accountId=acc_multi_b&limit=10")
    assert response.status_code == 200
    payload = response.json()["requests"]
    assert {entry["accountId"] for entry in payload} == {"acc_multi_a", "acc_multi_b"}


@pytest.mark.asyncio
async def test_request_logs_status_error_excludes_rate_limit_and_quota(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_err_only", "err-only@example.com"))

        await logs_repo.add_log(
            account_id="acc_err_only",
            request_id="req_err_rate_limit",
            model="gpt-5.1",
            input_tokens=1,
            output_tokens=1,
            latency_ms=10,
            status="error",
            error_code="rate_limit_exceeded",
            requested_at=now - timedelta(minutes=2),
        )
        await logs_repo.add_log(
            account_id="acc_err_only",
            request_id="req_err_quota",
            model="gpt-5.1",
            input_tokens=1,
            output_tokens=1,
            latency_ms=10,
            status="error",
            error_code="insufficient_quota",
            requested_at=now - timedelta(minutes=1),
        )
        await logs_repo.add_log(
            account_id="acc_err_only",
            request_id="req_err_other",
            model="gpt-5.1",
            input_tokens=1,
            output_tokens=1,
            latency_ms=10,
            status="error",
            error_code="upstream_error",
            error_message="upstream failure",
            requested_at=now,
        )

    response = await async_client.get("/api/request-logs?status=error&limit=10")
    assert response.status_code == 200
    payload = response.json()["requests"]
    assert len(payload) == 1
    assert payload[0]["status"] == "error"
    assert payload[0]["errorCode"] == "upstream_error"


@pytest.mark.asyncio
async def test_request_logs_search_matches_email_and_error(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_search", "example@myemail.com"))

        await logs_repo.add_log(
            account_id="acc_search",
            request_id="req_search_1",
            model="gpt-5.1",
            input_tokens=1,
            output_tokens=1,
            latency_ms=10,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=1),
        )
        await logs_repo.add_log(
            account_id="acc_search",
            request_id="req_search_2",
            model="gpt-5.1",
            input_tokens=1,
            output_tokens=1,
            latency_ms=10,
            status="error",
            error_code="upstream_error",
            error_message="This is an example string",
            requested_at=now,
        )

    response = await async_client.get("/api/request-logs?search=example&limit=50")
    assert response.status_code == 200
    payload = response.json()["requests"]
    assert {entry["requestId"] for entry in payload} == {"req_search_1", "req_search_2"}


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
            reasoning_effort="xhigh",
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
    assert entry["reasoningEffort"] == "xhigh"
    expected = round(_cost(1000, 400, 100), 6)
    assert entry["costUsd"] == pytest.approx(expected)


async def test_request_logs_cost_uses_priority_service_tier(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_priority", "priority@example.com"))

        await logs_repo.add_log(
            account_id="acc_priority",
            request_id="req_priority_1",
            model="gpt-5.4",
            service_tier="priority",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            latency_ms=50,
            status="success",
            error_code=None,
            requested_at=now,
        )

    response = await async_client.get("/api/request-logs?accountId=acc_priority&limit=1")
    assert response.status_code == 200
    payload = response.json()["requests"]
    assert len(payload) == 1
    entry = payload[0]
    assert entry["serviceTier"] == "priority"
    expected = round(_cost(1_000_000, 1_000_000, input_rate=5.0, cached_rate=0.5, output_rate=30.0), 6)
    assert entry["costUsd"] == pytest.approx(expected)


@pytest.mark.asyncio
async def test_request_logs_cost_uses_flex_service_tier(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_flex", "flex@example.com"))

        await logs_repo.add_log(
            account_id="acc_flex",
            request_id="req_flex_1",
            model="gpt-5.4",
            service_tier="flex",
            input_tokens=300_000,
            output_tokens=100_000,
            cached_input_tokens=50_000,
            latency_ms=50,
            status="success",
            error_code=None,
            requested_at=now,
        )

    response = await async_client.get("/api/request-logs?accountId=acc_flex&limit=1")
    assert response.status_code == 200
    payload = response.json()["requests"]
    assert len(payload) == 1
    entry = payload[0]
    assert entry["serviceTier"] == "flex"
    expected = round(_cost(300_000, 100_000, 50_000, input_rate=2.5, cached_rate=0.25, output_rate=11.25), 6)
    assert entry["costUsd"] == pytest.approx(expected)


@pytest.mark.asyncio
async def test_request_logs_cost_uses_persisted_cost_field(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_persisted_log_cost", "persisted-log-cost@example.com"))

        log = await logs_repo.add_log(
            account_id="acc_persisted_log_cost",
            request_id="req_persisted_log_cost_1",
            model="gpt-5.1",
            input_tokens=1000,
            output_tokens=500,
            latency_ms=50,
            status="success",
            error_code=None,
            requested_at=now,
        )
        await session.execute(update(log.__class__).where(log.__class__.id == log.id).values(cost_usd=4.321234))
        await session.commit()

    response = await async_client.get("/api/request-logs?accountId=acc_persisted_log_cost&limit=1")
    assert response.status_code == 200
    payload = response.json()["requests"]
    assert len(payload) == 1
    assert payload[0]["costUsd"] == pytest.approx(4.321234)


@pytest.mark.asyncio
async def test_request_logs_search_matches_api_key_name(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_key_search", "key-search@example.com"))
        session.add(
            ApiKey(
                id="key_search_1",
                name="Window-Runner",
                key_hash="hash_key_search_1",
                key_prefix="sk-test",
            )
        )
        await session.commit()

        await logs_repo.add_log(
            account_id="acc_key_search",
            request_id="req_key_search_1",
            model="gpt-5.1",
            input_tokens=3,
            output_tokens=2,
            latency_ms=10,
            status="success",
            error_code=None,
            requested_at=now,
            api_key_id="key_search_1",
        )

    response = await async_client.get("/api/request-logs?search=window-runner&limit=50")
    assert response.status_code == 200
    payload = response.json()["requests"]
    assert len(payload) == 1
    assert payload[0]["requestId"] == "req_key_search_1"
    assert payload[0]["apiKeyName"] == "Window-Runner"
