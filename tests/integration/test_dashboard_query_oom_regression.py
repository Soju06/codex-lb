from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import event

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, AdditionalUsageHistory
from app.db.session import SessionLocal, engine
from app.modules.accounts.repository import AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import AdditionalUsageRepository

pytestmark = pytest.mark.integration


def _make_account(account_id: str, email: str | None = None) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email or f"{account_id}@example.com",
        plan_type="pro",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


@pytest.mark.asyncio
async def test_additional_usage_latest_by_account_avoids_window_function(db_setup):
    now = utcnow()
    statements: list[str] = []

    def capture_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        repo = AdditionalUsageRepository(session)
        await accounts_repo.upsert(_make_account("acc1"))
        await accounts_repo.upsert(_make_account("acc2"))
        await repo.add_entry(
            "acc1",
            "gpt-5.3-codex-spark",
            "codex_bengalfox",
            "primary",
            10.0,
            recorded_at=now,
            quota_key="codex_spark",
        )
        await repo.add_entry(
            "acc1",
            "gpt-5.3-codex-spark",
            "codex_bengalfox",
            "primary",
            20.0,
            recorded_at=now,
            quota_key="codex_spark",
        )
        await repo.add_entry(
            "acc2",
            "gpt-5.3-codex-spark",
            "codex_bengalfox",
            "primary",
            30.0,
            recorded_at=now,
            quota_key="codex_spark",
        )

        event.listen(engine.sync_engine, "before_cursor_execute", capture_statement)
        try:
            latest = await repo.latest_by_quota_key(
                "codex_spark",
                "primary",
                account_ids=["acc1", "acc2"],
            )
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", capture_statement)

    assert set(latest) == {"acc1", "acc2"}
    assert latest["acc1"].used_percent == 20.0
    assert latest["acc2"].used_percent == 30.0
    emitted_sql = "\n".join(statements).lower()
    assert "row_number" not in emitted_sql
    assert " over " not in emitted_sql
    assert "additional_usage_history.quota_key" in emitted_sql
    if session.bind and session.bind.dialect.name == "postgresql":
        assert "lower(additional_usage_history.limit_name)" not in emitted_sql
        assert "lower(additional_usage_history.metered_feature)" not in emitted_sql


@pytest.mark.asyncio
async def test_additional_usage_latest_by_account_preserves_alias_fallback(db_setup):
    now = utcnow()

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        repo = AdditionalUsageRepository(session)
        await accounts_repo.upsert(_make_account("acc_alias"))
        await repo.add_entry(
            "acc_alias",
            "gpt-5.3-codex-spark",
            "codex_bengalfox",
            "primary",
            10.0,
            recorded_at=now - timedelta(minutes=5),
            quota_key="codex_spark",
        )
        session.add(
            AdditionalUsageHistory(
                account_id="acc_alias",
                quota_key="legacy_unknown",
                limit_name="GPT-5.3-Codex-Spark",
                metered_feature="legacy-feature",
                window="primary",
                used_percent=42.0,
                recorded_at=now,
            )
        )
        await session.commit()

        latest = await repo.latest_by_quota_key(
            "codex_spark",
            "primary",
            account_ids=["acc_alias"],
        )

    assert set(latest) == {"acc_alias"}
    assert latest["acc_alias"].quota_key == "legacy_unknown"
    assert latest["acc_alias"].used_percent == 42.0


@pytest.mark.asyncio
async def test_request_usage_summary_by_account_does_not_rank_request_logs(db_setup):
    now = utcnow()
    statements: list[str] = []

    def capture_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        repo = AccountsRepository(session)
        await accounts_repo.upsert(_make_account("acc1"))
        await logs_repo.add_log(
            account_id="acc1",
            request_id="req1",
            model="gpt-5.4-mini",
            input_tokens=10,
            output_tokens=5,
            cached_input_tokens=3,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now,
        )
        await logs_repo.add_log(
            account_id="acc1",
            request_id="req2",
            model="gpt-5.4-mini",
            input_tokens=7,
            output_tokens=None,
            reasoning_tokens=11,
            cached_input_tokens=2,
            latency_ms=100,
            status="error",
            error_code="server_error",
            requested_at=now,
        )

        event.listen(engine.sync_engine, "before_cursor_execute", capture_statement)
        try:
            usage = await repo.list_request_usage_summary_by_account(["acc1"])
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", capture_statement)

    assert usage["acc1"].request_count == 2
    assert usage["acc1"].total_tokens == 33
    assert usage["acc1"].cached_input_tokens == 5
    emitted_sql = "\n".join(statements).lower()
    assert "row_number" not in emitted_sql
    assert " over " not in emitted_sql
