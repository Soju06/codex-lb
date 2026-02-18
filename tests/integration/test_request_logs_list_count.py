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

        logs, total = await repo.list_recent(limit=3, offset=0)
        assert len(logs) == 3
        assert total == 5


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

        page1_logs, page1_total = await repo.list_recent(limit=3, offset=0)
        page2_logs, page2_total = await repo.list_recent(limit=3, offset=3)
        assert len(page1_logs) == 3
        assert len(page2_logs) == 3
        assert page1_total == 10
        assert page2_total == 10


@pytest.mark.asyncio
async def test_list_recent_empty_returns_zero_total(db_setup):
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)
        logs, total = await repo.list_recent(limit=10)
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

        logs, total = await repo.list_recent(limit=3, offset=10)
        assert logs == []
        assert total == 4
