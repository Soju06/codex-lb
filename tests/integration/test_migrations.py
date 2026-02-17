from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.auth import DEFAULT_PLAN
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.migrate import LEGACY_MIGRATION_ORDER, run_startup_migrations
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository

pytestmark = pytest.mark.integration
_DATABASE_URL = get_settings().database_url


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


@pytest.mark.asyncio
async def test_run_startup_migrations_preserves_unknown_plan_types(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_make_account("acc_one", "one@example.com", "education"))
        await repo.upsert(_make_account("acc_two", "two@example.com", "PRO"))
        await repo.upsert(_make_account("acc_three", "three@example.com", ""))

    result = await run_startup_migrations(_DATABASE_URL)
    assert result.current_revision == "010_add_idx_logs_requested_at"
    assert result.bootstrap.stamped_revision is None

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

    rerun = await run_startup_migrations(_DATABASE_URL)
    assert rerun.current_revision == "010_add_idx_logs_requested_at"


@pytest.mark.asyncio
async def test_run_startup_migrations_bootstraps_legacy_history(db_setup):
    async with SessionLocal() as session:
        await session.execute(
            text(
                """
                CREATE TABLE schema_migrations (
                    name TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
        )
        for index, migration_name in enumerate(LEGACY_MIGRATION_ORDER[:4]):
            await session.execute(
                text("INSERT INTO schema_migrations (name, applied_at) VALUES (:name, :applied_at)"),
                {"name": migration_name, "applied_at": f"2026-02-13T00:00:0{index}Z"},
            )
        await session.commit()

    result = await run_startup_migrations(_DATABASE_URL)

    assert result.bootstrap.stamped_revision == "004_add_accounts_chatgpt_account_id"
    assert result.current_revision == "010_add_idx_logs_requested_at"

    async with SessionLocal() as session:
        revision_rows = await session.execute(text("SELECT version_num FROM alembic_version"))
        revisions = [str(row[0]) for row in revision_rows.fetchall()]
        assert revisions == ["010_add_idx_logs_requested_at"]


@pytest.mark.asyncio
async def test_run_startup_migrations_skips_legacy_stamp_when_required_tables_missing(db_setup):
    async with SessionLocal() as session:
        await session.execute(text("DROP TABLE dashboard_settings"))
        await session.execute(
            text(
                """
                CREATE TABLE schema_migrations (
                    name TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
        )
        for index, migration_name in enumerate(LEGACY_MIGRATION_ORDER[:4]):
            await session.execute(
                text("INSERT INTO schema_migrations (name, applied_at) VALUES (:name, :applied_at)"),
                {"name": migration_name, "applied_at": f"2026-02-13T00:00:0{index}Z"},
            )
        await session.commit()

    result = await run_startup_migrations(_DATABASE_URL)

    assert result.bootstrap.stamped_revision is None
    assert result.current_revision == "010_add_idx_logs_requested_at"

    async with SessionLocal() as session:
        setting_id = await session.execute(text("SELECT id FROM dashboard_settings WHERE id = 1"))
        assert setting_id.scalar_one() == 1


@pytest.mark.asyncio
async def test_run_startup_migrations_handles_unknown_legacy_rows(db_setup):
    async with SessionLocal() as session:
        await session.execute(
            text(
                """
                CREATE TABLE schema_migrations (
                    name TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
        )
        await session.execute(
            text("INSERT INTO schema_migrations (name, applied_at) VALUES (:name, :applied_at)"),
            {"name": "001_normalize_account_plan_types", "applied_at": "2026-02-13T00:00:00Z"},
        )
        await session.execute(
            text("INSERT INTO schema_migrations (name, applied_at) VALUES (:name, :applied_at)"),
            {"name": "900_custom_hotfix", "applied_at": "2026-02-13T00:00:01Z"},
        )
        await session.commit()

    result = await run_startup_migrations(_DATABASE_URL)

    assert result.bootstrap.stamped_revision == "001_normalize_account_plan_types"
    assert result.bootstrap.unknown_migrations == ("900_custom_hotfix",)
    assert result.current_revision == "010_add_idx_logs_requested_at"
