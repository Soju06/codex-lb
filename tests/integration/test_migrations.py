from __future__ import annotations

import pytest
from sqlalchemy import inspect

from app.core.auth import DEFAULT_PLAN
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.migrations import MIGRATIONS, run_migrations
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository

pytestmark = pytest.mark.integration


def _make_account(account_id: str, email: str, plan_type: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        plan_type=plan_type,
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


def _usage_history_columns(sync_session) -> set[str]:
    connection = sync_session.connection()
    return {column["name"] for column in inspect(connection).get_columns("usage_history")}


@pytest.mark.asyncio
async def test_run_migrations_preserves_unknown_plan_types(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_make_account("acc_one", "one@example.com", "education"))
        await repo.upsert(_make_account("acc_two", "two@example.com", "PRO"))
        await repo.upsert(_make_account("acc_three", "three@example.com", ""))

    async with SessionLocal() as session:
        applied = await run_migrations(session)
        assert applied == len(MIGRATIONS)
        columns = await session.run_sync(_usage_history_columns)
        assert "window_label" in columns

    async with SessionLocal() as session:
        acc_one = await session.get(Account, "acc_one")
        acc_two = await session.get(Account, "acc_two")
        acc_three = await session.get(Account, "acc_three")
        assert acc_one is not None
        assert acc_two is not None
        assert acc_three is not None
        assert acc_one.plan_type == "education"
        assert acc_two.plan_type == "pro"
        assert acc_three.plan_type == DEFAULT_PLAN

    async with SessionLocal() as session:
        applied = await run_migrations(session)
        assert applied == 0
