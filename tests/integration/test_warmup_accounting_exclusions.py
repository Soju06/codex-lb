from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, ApiKey
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
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
async def test_warmup_request_logs_are_excluded_from_dashboard_api_key_and_account_aggregates(db_setup):
    del db_setup
    now = utcnow()

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        api_keys_repo = ApiKeysRepository(session)

        await accounts_repo.upsert(_make_account("acc-warmup-exclusion", "warmup-exclusion@example.com"))
        session.add(
            ApiKey(
                id="key-warmup-exclusion",
                name="Warmup Exclusion Key",
                key_hash="hash-warmup-exclusion",
                key_prefix="sk-test",
            )
        )
        await session.commit()

        await logs_repo.add_log(
            account_id="acc-warmup-exclusion",
            api_key_id="key-warmup-exclusion",
            request_id="req-normal",
            request_kind="normal",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=5,
            cached_input_tokens=3,
            latency_ms=100,
            status="error",
            error_code="rate_limit_exceeded",
            requested_at=now - timedelta(minutes=5),
        )
        await logs_repo.add_log(
            account_id="acc-warmup-exclusion",
            api_key_id="key-warmup-exclusion",
            request_id="req-warmup",
            request_kind="warmup",
            model="gpt-5.4-mini",
            input_tokens=100,
            output_tokens=50,
            cached_input_tokens=25,
            latency_ms=120,
            status="error",
            error_code="quota_exceeded",
            requested_at=now - timedelta(minutes=4),
        )

        since = now - timedelta(hours=1)
        until = now + timedelta(hours=1)

        activity = await logs_repo.aggregate_activity_since(since)
        assert activity.request_count == 1
        assert activity.error_count == 1
        assert activity.input_tokens == 10
        assert activity.output_tokens == 5
        assert activity.cached_input_tokens == 3

        top_error = await logs_repo.top_error_since(since)
        assert top_error == "rate_limit_exceeded"

        buckets = await logs_repo.aggregate_by_bucket(since, bucket_seconds=3600)
        assert len(buckets) == 1
        assert buckets[0].request_count == 1
        assert buckets[0].error_count == 1
        assert buckets[0].input_tokens == 10
        assert buckets[0].output_tokens == 5

        key_summary = await api_keys_repo.get_usage_summary_by_key_id("key-warmup-exclusion")
        assert key_summary.request_count == 1
        assert key_summary.total_tokens == 15
        assert key_summary.cached_input_tokens == 3

        key_trends = await api_keys_repo.trends_by_key("key-warmup-exclusion", since, until, bucket_seconds=3600)
        assert len(key_trends) == 1
        assert key_trends[0].total_tokens == 15

        key_usage_7d = await api_keys_repo.usage_7d("key-warmup-exclusion", since, until)
        assert key_usage_7d.total_requests == 1
        assert key_usage_7d.total_tokens == 15
        assert key_usage_7d.cached_input_tokens == 3

        account_usage = await accounts_repo.list_request_usage_summary_by_account(["acc-warmup-exclusion"])
        summary = account_usage["acc-warmup-exclusion"]
        assert summary.request_count == 1
        assert summary.total_tokens == 15
        assert summary.cached_input_tokens == 3
