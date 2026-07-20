from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import event

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal, engine
from app.modules.accounts.repository import AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository

pytestmark = pytest.mark.integration


def _make_account(account_id: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


@pytest.mark.asyncio
async def test_list_recent_returns_rows_and_total(db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc1"))

        for i in range(5):
            await repo.add_log(
                account_id="acc1",
                request_id=f"req_{i}",
                model="gpt-5.1",
                input_tokens=10,
                output_tokens=20,
                latency_ms=100,
                status="success",
                error_code=None,
                requested_at=now - timedelta(minutes=i),
            )

        result = await repo.list_recent(limit=3, offset=0)
        logs = result.logs
        total = result.total
        assert len(logs) == 3
        assert total == 5
        assert logs[0].plan_type == "plus"


@pytest.mark.asyncio
async def test_list_recent_pagination_total_stays_consistent(db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc1"))

        for i in range(10):
            await repo.add_log(
                account_id="acc1",
                request_id=f"req_page_{i}",
                model="gpt-5.1",
                input_tokens=10,
                output_tokens=20,
                latency_ms=100,
                status="success",
                error_code=None,
                requested_at=now - timedelta(minutes=i),
            )

        page1 = await repo.list_recent(limit=3, offset=0)
        page2 = await repo.list_recent(limit=3, offset=3)
        page1_logs = page1.logs
        page1_total = page1.total
        page2_logs = page2.logs
        page2_total = page2.total
        assert len(page1_logs) == 3
        assert len(page2_logs) == 3
        assert page1_total == 10
        assert page2_total == 10


@pytest.mark.asyncio
async def test_list_recent_empty_returns_zero_total(db_setup):
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)
        result = await repo.list_recent(limit=10)
        logs = result.logs
        total = result.total
        assert logs == []
        assert total == 0


@pytest.mark.asyncio
async def test_list_recent_offset_past_end_preserves_total(db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc1"))

        for i in range(4):
            await repo.add_log(
                account_id="acc1",
                request_id=f"req_offset_{i}",
                model="gpt-5.1",
                input_tokens=10,
                output_tokens=20,
                latency_ms=100,
                status="success",
                error_code=None,
                requested_at=now - timedelta(minutes=i),
            )

        result = await repo.list_recent(limit=3, offset=10)
        logs = result.logs
        total = result.total
        assert logs == []
        assert total == 4


@pytest.mark.asyncio
async def test_list_recent_without_search_avoids_related_joins(db_setup):
    statements: list[str] = []

    def _capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _capture)
    try:
        now = utcnow()
        async with SessionLocal() as session:
            accounts_repo = AccountsRepository(session)
            repo = RequestLogsRepository(session)
            await accounts_repo.upsert(_make_account("acc1"))
            await repo.add_log(
                account_id="acc1",
                request_id="req_joinless_1",
                model="gpt-5.1",
                input_tokens=10,
                output_tokens=20,
                latency_ms=100,
                status="success",
                error_code=None,
                requested_at=now,
            )

            statements.clear()
            result = await repo.list_recent(limit=3, offset=0)
            logs = result.logs
            total = result.total

        assert len(logs) == 1
        assert total == 1
        select_statements = [statement for statement in statements if "FROM request_logs" in statement]
        assert select_statements
        assert all("JOIN accounts" not in statement for statement in select_statements)
        assert all("JOIN api_keys" not in statement for statement in select_statements)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _capture)


@pytest.mark.asyncio
async def test_list_recent_uses_separate_count_instead_of_window_count(db_setup):
    statements: list[str] = []

    def _capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _capture)
    try:
        now = utcnow()
        async with SessionLocal() as session:
            accounts_repo = AccountsRepository(session)
            repo = RequestLogsRepository(session)
            await accounts_repo.upsert(_make_account("acc_window_count"))
            for i in range(5):
                await repo.add_log(
                    account_id="acc_window_count",
                    request_id=f"req_window_count_{i}",
                    model="gpt-5.1",
                    input_tokens=10,
                    output_tokens=20,
                    latency_ms=100,
                    status="success",
                    error_code=None,
                    requested_at=now - timedelta(minutes=i),
                )

            statements.clear()
            result = await repo.list_recent(limit=3, offset=0)
            logs = result.logs
            total = result.total

        assert len(logs) == 3
        assert total == 5
        request_log_selects = [statement for statement in statements if "FROM request_logs" in statement]
        assert len(request_log_selects) >= 2
        assert any("count(" in statement.lower() for statement in request_log_selects)
        assert all("OVER" not in statement.upper() for statement in request_log_selects)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _capture)


@pytest.mark.asyncio
async def test_list_recent_with_search_keeps_related_joins(db_setup):
    statements: list[str] = []

    def _capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _capture)
    try:
        now = utcnow()
        async with SessionLocal() as session:
            accounts_repo = AccountsRepository(session)
            repo = RequestLogsRepository(session)
            await accounts_repo.upsert(_make_account("acc_search"))
            await repo.add_log(
                account_id="acc_search",
                request_id="req_join_1",
                model="gpt-5.1",
                input_tokens=10,
                output_tokens=20,
                latency_ms=100,
                status="success",
                error_code=None,
                requested_at=now,
            )

            statements.clear()
            result = await repo.list_recent(limit=3, offset=0, search="example.com")
            logs = result.logs
            total = result.total

        assert len(logs) == 1
        assert total == 1
        select_statements = [statement for statement in statements if "FROM request_logs" in statement]
        assert select_statements
        assert any("JOIN accounts" in statement for statement in select_statements)
        assert any("JOIN api_keys" in statement for statement in select_statements)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _capture)


@pytest.mark.asyncio
async def test_list_recent_count_cache_key_includes_conversation_id(db_setup, monkeypatch):
    from app.modules.request_logs import repository as logs_repository_module

    monkeypatch.setattr(logs_repository_module, "_COUNT_CACHE_TTL_SECONDS", 30.0)
    logs_repository_module._clear_recent_count_cache()
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)
        for request_id, conversation_id in (
            ("req_cache_conv_a_1", "conv-a"),
            ("req_cache_conv_a_2", "conv-a"),
            ("req_cache_conv_b", "conv-b"),
        ):
            await repo.add_log(
                account_id=None,
                request_id=request_id,
                model="gpt-5.1",
                input_tokens=1,
                output_tokens=1,
                latency_ms=10,
                status="success",
                error_code=None,
                conversation_id=conversation_id,
            )

        result_a = await repo.list_recent(conversation_id="conv-a")
        result_b = await repo.list_recent(conversation_id="conv-b")

    logs_repository_module._clear_recent_count_cache()
    assert result_a.total == 2
    assert result_b.total == 1
