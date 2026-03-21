from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.usage.repository import UsageRepository

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


def _dialect_name(session: AsyncSession) -> str:
    bind = session.get_bind()
    return bind.dialect.name if bind is not None else "sqlite"


@pytest.mark.asyncio
async def test_latest_by_account_returns_single_latest_per_account(db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account("acc1"))
        await accounts_repo.upsert(_make_account("acc2"))

        await repo.add_entry("acc1", 10.0, window="primary", recorded_at=now - timedelta(hours=2))
        await repo.add_entry("acc1", 30.0, window="primary", recorded_at=now - timedelta(hours=1))
        await repo.add_entry("acc1", 50.0, window="primary", recorded_at=now)
        await repo.add_entry("acc2", 20.0, window="primary", recorded_at=now - timedelta(hours=1))
        await repo.add_entry("acc2", 40.0, window="primary", recorded_at=now)

        latest = await repo.latest_by_account(window="primary")
        assert set(latest.keys()) == {"acc1", "acc2"}
        assert latest["acc1"].used_percent == 50.0
        assert latest["acc2"].used_percent == 40.0


@pytest.mark.asyncio
async def test_latest_by_account_respects_window_filter(db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account("acc1"))

        await repo.add_entry("acc1", 10.0, window="primary", recorded_at=now - timedelta(hours=1))
        await repo.add_entry("acc1", 80.0, window="secondary", recorded_at=now)

        primary = await repo.latest_by_account(window="primary")
        assert "acc1" in primary
        assert primary["acc1"].used_percent == 10.0

        secondary = await repo.latest_by_account(window="secondary")
        assert "acc1" in secondary
        assert secondary["acc1"].used_percent == 80.0


@pytest.mark.asyncio
async def test_latest_by_account_default_includes_primary_and_none(db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account("acc1"))
        await accounts_repo.upsert(_make_account("acc2"))

        await repo.add_entry("acc1", 15.0, window=None, recorded_at=now - timedelta(hours=1))
        await repo.add_entry("acc1", 25.0, window="primary", recorded_at=now)
        await repo.add_entry("acc2", 35.0, window=None, recorded_at=now)

        latest = await repo.latest_by_account()
        assert set(latest.keys()) == {"acc1", "acc2"}
        assert latest["acc1"].used_percent == 25.0
        assert latest["acc2"].used_percent == 35.0


@pytest.mark.asyncio
async def test_latest_by_account_uses_recorded_at_with_deterministic_tie_breaker(db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account("acc1"))

        await repo.add_entry("acc1", 20.0, window="primary", recorded_at=now)
        await repo.add_entry("acc1", 30.0, window="primary", recorded_at=now)
        await repo.add_entry("acc1", 5.0, window="primary", recorded_at=now - timedelta(hours=6))

        latest = await repo.latest_by_account(window="primary")
        assert latest["acc1"].used_percent == 30.0


@pytest.mark.asyncio
async def test_latest_by_account_primary_query_plan_uses_normalized_window_index(db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        if _dialect_name(session) != "sqlite":
            pytest.skip("SQLite-only query plan test")

        accounts_repo = AccountsRepository(session)
        repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account("acc1"))
        await accounts_repo.upsert(_make_account("acc2"))

        await repo.add_entry("acc1", 10.0, window=None, recorded_at=now - timedelta(hours=2))
        await repo.add_entry("acc1", 20.0, window="primary", recorded_at=now)
        await repo.add_entry("acc2", 30.0, window=None, recorded_at=now - timedelta(hours=1))
        await repo.add_entry("acc2", 40.0, window="secondary", recorded_at=now)

        plan_rows = (
            await session.execute(
                text(
                    """
                    EXPLAIN QUERY PLAN
                    SELECT uh.id
                    FROM usage_history AS uh
                    JOIN (
                        SELECT id AS usage_id,
                               row_number() OVER (
                                   PARTITION BY account_id
                                   ORDER BY recorded_at DESC, id DESC
                               ) AS row_number
                        FROM usage_history
                        WHERE coalesce("window", 'primary') = 'primary'
                    ) AS ranked ON uh.id = ranked.usage_id
                    WHERE ranked.row_number = 1
                    """
                )
            )
        ).fetchall()

    details = " ".join(str(row[-1]) for row in plan_rows)
    assert "idx_usage_window_account_latest" in details


@pytest.mark.asyncio
async def test_latest_by_account_primary_query_plan_uses_normalized_window_index_postgresql(db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        if _dialect_name(session) != "postgresql":
            pytest.skip("PostgreSQL-only query plan test")

        accounts_repo = AccountsRepository(session)
        repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account("acc1"))
        await accounts_repo.upsert(_make_account("acc2"))

        await repo.add_entry("acc1", 10.0, window=None, recorded_at=now - timedelta(hours=2))
        await repo.add_entry("acc1", 20.0, window="primary", recorded_at=now)
        await repo.add_entry("acc2", 30.0, window=None, recorded_at=now - timedelta(hours=1))
        await repo.add_entry("acc2", 40.0, window="secondary", recorded_at=now)

        await session.execute(text("SET enable_seqscan = off"))
        plan = (
            await session.execute(
                text(
                    """
                    EXPLAIN (FORMAT JSON)
                    SELECT DISTINCT ON (account_id) id
                    FROM usage_history
                    WHERE coalesce("window", 'primary') = 'primary'
                    ORDER BY account_id ASC, recorded_at DESC, id DESC
                    """
                )
            )
        ).scalar_one()

    plan_json = json.dumps(plan)
    assert "idx_usage_window_account_latest" in plan_json or "idx_usage_window_account_time" in plan_json
    assert "Seq Scan" not in plan_json
