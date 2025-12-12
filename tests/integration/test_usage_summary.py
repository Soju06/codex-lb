from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import UsageRepository
from app.modules.usage.service import UsageService

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
async def test_usage_summary_cost_includes_cached_tokens(db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        usage_repo = UsageRepository(session)
        service = UsageService(usage_repo, logs_repo, accounts_repo)

        await accounts_repo.upsert(_make_account("acc1", "cached@example.com"))

        now = utcnow()
        await logs_repo.add_log(
            account_id="acc1",
            request_id="req_summary_1",
            model="gpt-5.1",
            input_tokens=1000,
            output_tokens=500,
            cached_input_tokens=200,
            reasoning_tokens=None,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=5),
        )

        summary = await service.get_usage_summary()
        cost = summary.cost

        expected_raw = (800 / 1_000_000) * 1.25 + (200 / 1_000_000) * 0.125 + (500 / 1_000_000) * 10.0
        expected = round(expected_raw, 6)
        assert cost.total_usd_7d == pytest.approx(expected)
        by_model = {entry.model: entry.usd for entry in cost.by_model}
        assert by_model["gpt-5.1"] == pytest.approx(expected)


@pytest.mark.asyncio
async def test_usage_summary_metrics(db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        usage_repo = UsageRepository(session)
        service = UsageService(usage_repo, logs_repo, accounts_repo)

        await accounts_repo.upsert(_make_account("acc2", "metrics@example.com"))

        now = utcnow()
        await logs_repo.add_log(
            account_id="acc2",
            request_id="req_summary_2",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=20,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now - timedelta(hours=2),
        )
        await logs_repo.add_log(
            account_id="acc2",
            request_id="req_summary_3",
            model="gpt-5.1",
            input_tokens=5,
            output_tokens=0,
            latency_ms=50,
            status="error",
            error_code="rate_limit_exceeded",
            requested_at=now - timedelta(hours=1),
        )

        summary = await service.get_usage_summary()
        metrics = summary.metrics
        assert metrics is not None
        assert metrics.requests_7d == 2
        assert metrics.tokens_secondary_window == 35
        assert metrics.error_rate_7d == pytest.approx(0.5)
        assert metrics.top_error == "rate_limit_exceeded"
