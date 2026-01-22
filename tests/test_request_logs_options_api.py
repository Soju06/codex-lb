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


@pytest.mark.asyncio
async def test_request_logs_options_returns_distinct_accounts_and_models(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_opt_a", "a@example.com"))
        await accounts_repo.upsert(_make_account("acc_opt_b", "b@example.com"))

        await logs_repo.add_log(
            account_id="acc_opt_a",
            request_id="req_opt_1",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=10,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=1),
        )
        await logs_repo.add_log(
            account_id="acc_opt_b",
            request_id="req_opt_2",
            model="gpt-4o",
            input_tokens=10,
            output_tokens=10,
            latency_ms=100,
            status="error",
            error_code="rate_limit_exceeded",
            error_message="Rate limit reached",
            requested_at=now,
        )

    response = await async_client.get("/api/request-logs/options")
    assert response.status_code == 200
    payload = response.json()
    assert payload["accountIds"] == ["acc_opt_a", "acc_opt_b"]
    assert payload["modelOptions"] == [
        {"model": "gpt-4o", "reasoningEffort": None},
        {"model": "gpt-5.1", "reasoningEffort": None},
    ]


@pytest.mark.asyncio
async def test_request_logs_options_respects_status_filter(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_opt_ok", "ok@example.com"))
        await accounts_repo.upsert(_make_account("acc_opt_err", "err@example.com"))

        await logs_repo.add_log(
            account_id="acc_opt_ok",
            request_id="req_opt_ok",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=10,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now,
        )
        await logs_repo.add_log(
            account_id="acc_opt_err",
            request_id="req_opt_err",
            model="gpt-4o",
            input_tokens=10,
            output_tokens=10,
            latency_ms=100,
            status="error",
            error_code="rate_limit_exceeded",
            requested_at=now,
        )

    response = await async_client.get("/api/request-logs/options?status=ok")
    assert response.status_code == 200
    payload = response.json()
    assert payload["accountIds"] == ["acc_opt_ok"]
    assert payload["modelOptions"] == [{"model": "gpt-5.1", "reasoningEffort": None}]
