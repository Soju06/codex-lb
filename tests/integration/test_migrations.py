from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.auth import DEFAULT_PLAN
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow

try:
    from app.db.alembic.revision_ids import OLD_TO_NEW_REVISION_MAP

    _HAS_REVISION_REMAP = True
except ImportError:
    OLD_TO_NEW_REVISION_MAP = {
        "001_normalize_account_plan_types": "001_normalize_account_plan_types",
        "004_add_accounts_chatgpt_account_id": "004_add_accounts_chatgpt_account_id",
    }
    _HAS_REVISION_REMAP = False

from app.db.migrate import (
    LEGACY_MIGRATION_ORDER,
    check_schema_drift,
    inspect_migration_state,
    run_startup_migrations,
)
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository

try:
    from app.db.migrate import check_migration_policy
except ImportError:
    check_migration_policy = None  # type: ignore[assignment]
pytestmark = pytest.mark.integration
_DATABASE_URL = get_settings().database_url
_HEAD_REVISION = inspect_migration_state(_DATABASE_URL).head_revision
_STAMPED_AFTER_LEGACY_PREFIX_4 = OLD_TO_NEW_REVISION_MAP["004_add_accounts_chatgpt_account_id"]
_STAMPED_AFTER_LEGACY_PREFIX_1 = OLD_TO_NEW_REVISION_MAP["001_normalize_account_plan_types"]


def _expected_head_revisions() -> list[str]:
    return sorted(revision for revision in _HEAD_REVISION.split(",") if revision)


def _normalize_revision_rows(raw_revisions: list[str]) -> list[str]:
    revisions: set[str] = set()
    for raw in raw_revisions:
        revisions.update(part for part in str(raw).split(",") if part)
    return sorted(revisions)


async def _read_alembic_version_revisions() -> list[str]:
    async with SessionLocal() as session:
        revision_rows = await session.execute(text("SELECT version_num FROM alembic_version"))
        raw = [str(row[0]) for row in revision_rows.fetchall()]
    return _normalize_revision_rows(raw)


async def _replace_alembic_versions_with_single_revision(revision: str) -> None:
    async with SessionLocal() as session:
        await session.execute(text("DELETE FROM alembic_version"))
        await session.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": revision},
        )
        await session.commit()


def _is_postgresql_database_url(url: str) -> bool:
    return url.startswith("postgresql+")


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
async def test_run_startup_migrations_preserves_unknown_plan_types(raw_db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_make_account("acc_one", "one@example.com", "education"))
        await repo.upsert(_make_account("acc_two", "two@example.com", "PRO"))
        await repo.upsert(_make_account("acc_three", "three@example.com", ""))

    result = await run_startup_migrations(_DATABASE_URL)
    assert result.current_revision == _HEAD_REVISION
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
    assert rerun.current_revision == _HEAD_REVISION


@pytest.mark.asyncio
async def test_run_startup_migrations_bootstraps_legacy_history(raw_db_setup):
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

    assert result.bootstrap.stamped_revision == _STAMPED_AFTER_LEGACY_PREFIX_4
    assert result.current_revision == _HEAD_REVISION

    revisions = await _read_alembic_version_revisions()
    assert revisions == _expected_head_revisions()


@pytest.mark.asyncio
async def test_run_startup_migrations_skips_legacy_stamp_when_required_tables_missing(raw_db_setup):
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
    assert result.current_revision == _HEAD_REVISION

    async with SessionLocal() as session:
        setting_id = await session.execute(text("SELECT id FROM dashboard_settings WHERE id = 1"))
        assert setting_id.scalar_one() == 1


@pytest.mark.asyncio
async def test_run_startup_migrations_handles_unknown_legacy_rows(raw_db_setup):
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

    assert result.bootstrap.stamped_revision == _STAMPED_AFTER_LEGACY_PREFIX_1
    assert result.bootstrap.unknown_migrations == ("900_custom_hotfix",)
    assert result.current_revision == _HEAD_REVISION


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAS_REVISION_REMAP, reason="requires revision remap support")
async def test_run_startup_migrations_auto_remaps_legacy_alembic_revision_ids(raw_db_setup):
    await run_startup_migrations(_DATABASE_URL)

    legacy_head = "013_add_dashboard_settings_routing_strategy"
    await _replace_alembic_versions_with_single_revision(legacy_head)

    result = await run_startup_migrations(_DATABASE_URL)
    assert result.current_revision == _HEAD_REVISION

    revisions = await _read_alembic_version_revisions()
    assert revisions == _expected_head_revisions()


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAS_REVISION_REMAP, reason="requires revision remap support")
async def test_run_startup_migrations_auto_remaps_firewall_legacy_revision_id(raw_db_setup):
    await run_startup_migrations(_DATABASE_URL)

    legacy_firewall_revision = "014_add_api_firewall_allowlist"
    await _replace_alembic_versions_with_single_revision(legacy_firewall_revision)

    result = await run_startup_migrations(_DATABASE_URL)
    assert result.current_revision == _HEAD_REVISION

    revisions = await _read_alembic_version_revisions()
    assert revisions == _expected_head_revisions()


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAS_REVISION_REMAP, reason="requires revision remap support")
async def test_run_startup_migrations_handles_legacy_schema_table_and_legacy_alembic_id_together(raw_db_setup):
    await run_startup_migrations(_DATABASE_URL)

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
        for index, migration_name in enumerate(LEGACY_MIGRATION_ORDER[:3]):
            await session.execute(
                text("INSERT INTO schema_migrations (name, applied_at) VALUES (:name, :applied_at)"),
                {"name": migration_name, "applied_at": f"2026-02-13T00:00:0{index}Z"},
            )
        await session.execute(text("DELETE FROM alembic_version"))
        await session.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:legacy)"),
            {"legacy": "013_add_dashboard_settings_routing_strategy"},
        )
        await session.commit()

    result = await run_startup_migrations(_DATABASE_URL)
    assert result.bootstrap.stamped_revision is None
    assert result.current_revision == _HEAD_REVISION


@pytest.mark.asyncio
@pytest.mark.skipif(
    (not _is_postgresql_database_url(_DATABASE_URL)) or check_migration_policy is None,
    reason="PostgreSQL-only migration contract test",
)
async def test_postgresql_migration_contract_policy_and_drift_match(raw_db_setup):
    result = await run_startup_migrations(_DATABASE_URL)
    assert result.current_revision == _HEAD_REVISION

    assert check_migration_policy(_DATABASE_URL) == ()
    assert check_schema_drift(_DATABASE_URL) == ()


@pytest.mark.asyncio
@pytest.mark.skipif(
    (not _is_postgresql_database_url(_DATABASE_URL)) or (not _HAS_REVISION_REMAP),
    reason="PostgreSQL-only migration remap test",
)
async def test_postgresql_startup_migration_auto_remap_legacy_head(raw_db_setup):
    await run_startup_migrations(_DATABASE_URL)

    async with SessionLocal() as session:
        await session.execute(
            text("UPDATE alembic_version SET version_num = :legacy"),
            {"legacy": "013_add_dashboard_settings_routing_strategy"},
        )
        await session.commit()

    result = await run_startup_migrations(_DATABASE_URL)
    assert result.current_revision == _HEAD_REVISION

    async with SessionLocal() as session:
        version_num = (await session.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))).scalar_one()
        assert str(version_num) == _HEAD_REVISION


@pytest.mark.asyncio
async def test_run_startup_migrations_drops_accounts_email_unique_with_non_cascade_fks(tmp_path):
    db_path = tmp_path / "legacy-no-cascade.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            await session.execute(text("PRAGMA foreign_keys=ON"))
            await session.execute(
                text(
                    """
                    CREATE TABLE accounts (
                        id VARCHAR NOT NULL PRIMARY KEY,
                        chatgpt_account_id VARCHAR,
                        email VARCHAR NOT NULL UNIQUE,
                        plan_type VARCHAR NOT NULL,
                        access_token_encrypted BLOB NOT NULL,
                        refresh_token_encrypted BLOB NOT NULL,
                        id_token_encrypted BLOB NOT NULL,
                        last_refresh DATETIME NOT NULL,
                        created_at DATETIME NOT NULL,
                        status VARCHAR(32) NOT NULL,
                        deactivation_reason TEXT,
                        reset_at INTEGER
                    )
                    """
                )
            )
            await session.execute(
                text(
                    """
                    CREATE TABLE usage_history (
                        id INTEGER PRIMARY KEY,
                        account_id VARCHAR NOT NULL REFERENCES accounts(id),
                        recorded_at DATETIME NOT NULL,
                        window VARCHAR,
                        used_percent FLOAT NOT NULL,
                        input_tokens INTEGER,
                        output_tokens INTEGER,
                        reset_at INTEGER,
                        window_minutes INTEGER,
                        credits_has BOOLEAN,
                        credits_unlimited BOOLEAN,
                        credits_balance FLOAT
                    )
                    """
                )
            )
            await session.execute(
                text(
                    """
                    CREATE TABLE request_logs (
                        id INTEGER PRIMARY KEY,
                        account_id VARCHAR NOT NULL REFERENCES accounts(id),
                        request_id VARCHAR NOT NULL,
                        requested_at DATETIME NOT NULL,
                        model VARCHAR NOT NULL,
                        input_tokens INTEGER,
                        output_tokens INTEGER,
                        cached_input_tokens INTEGER,
                        reasoning_tokens INTEGER,
                        reasoning_effort VARCHAR,
                        latency_ms INTEGER,
                        status VARCHAR NOT NULL,
                        error_code VARCHAR,
                        error_message TEXT
                    )
                    """
                )
            )
            await session.execute(
                text(
                    """
                    CREATE TABLE sticky_sessions (
                        key VARCHAR PRIMARY KEY,
                        account_id VARCHAR NOT NULL REFERENCES accounts(id),
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL
                    )
                    """
                )
            )
            await session.execute(
                text(
                    """
                    CREATE TABLE dashboard_settings (
                        id INTEGER PRIMARY KEY,
                        sticky_threads_enabled BOOLEAN NOT NULL,
                        prefer_earlier_reset_accounts BOOLEAN NOT NULL,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL
                    )
                    """
                )
            )
            await session.execute(
                text(
                    """
                    INSERT INTO dashboard_settings (
                        id, sticky_threads_enabled, prefer_earlier_reset_accounts, created_at, updated_at
                    ) VALUES (1, 0, 0, '2026-01-01 00:00:00', '2026-01-01 00:00:00')
                    """
                )
            )
            await session.execute(
                text(
                    """
                    INSERT INTO accounts (
                        id, chatgpt_account_id, email, plan_type,
                        access_token_encrypted, refresh_token_encrypted, id_token_encrypted,
                        last_refresh, created_at, status, deactivation_reason, reset_at
                    )
                    VALUES (
                        'acc_legacy', 'chatgpt_legacy', 'legacy@example.com', 'plus',
                        x'01', x'02', x'03',
                        '2026-01-01 00:00:00', '2026-01-01 00:00:00', 'active', NULL, NULL
                    )
                    """
                )
            )
            await session.execute(
                text(
                    """
                    INSERT INTO usage_history (
                        id, account_id, recorded_at, window, used_percent,
                        input_tokens, output_tokens, reset_at, window_minutes,
                        credits_has, credits_unlimited, credits_balance
                    )
                    VALUES (
                        1, 'acc_legacy', '2026-01-01 00:00:00', 'hour', 0.2,
                        10, 20, NULL, 60, 1, 0, 50.0
                    )
                    """
                )
            )
            await session.execute(
                text(
                    """
                    INSERT INTO request_logs (
                        id, account_id, request_id, requested_at, model, input_tokens, output_tokens,
                        cached_input_tokens, reasoning_tokens, reasoning_effort, latency_ms, status,
                        error_code, error_message
                    )
                    VALUES (
                        1, 'acc_legacy', 'req_1', '2026-01-01 00:00:00', 'gpt-4o', 10, 20,
                        0, 0, NULL, 100, 'ok', NULL, NULL
                    )
                    """
                )
            )
            await session.execute(
                text(
                    """
                    INSERT INTO sticky_sessions (key, account_id, created_at, updated_at)
                    VALUES ('sticky_1', 'acc_legacy', '2026-01-01 00:00:00', '2026-01-01 00:00:00')
                    """
                )
            )
            await session.commit()

        result = await run_startup_migrations(db_url)
        assert result.current_revision == _HEAD_REVISION

        async with session_factory() as session:
            await session.execute(text("PRAGMA foreign_keys=ON"))
            dashboard_columns_rows = (await session.execute(text("PRAGMA table_info(dashboard_settings)"))).fetchall()
            dashboard_columns = {str(row[1]) for row in dashboard_columns_rows if len(row) > 1}
            if "routing_strategy" in dashboard_columns:
                routing_strategy = (
                    await session.execute(text("SELECT routing_strategy FROM dashboard_settings WHERE id=1"))
                ).scalar_one()
                assert routing_strategy == "usage_weighted"
            http_proxy_url = (
                await session.execute(text("SELECT http_proxy_url FROM dashboard_settings WHERE id=1"))
            ).scalar_one()
            assert http_proxy_url is None
            index_rows = (await session.execute(text("PRAGMA index_list(accounts)"))).fetchall()
            has_email_non_unique_index = False
            for row in index_rows:
                if len(row) < 3:
                    continue
                index_name = str(row[1])
                is_unique = bool(row[2])
                escaped_name = index_name.replace('"', '""')
                index_info_rows = (await session.execute(text(f'PRAGMA index_info("{escaped_name}")'))).fetchall()
                column_names = [str(info[2]) for info in index_info_rows if len(info) > 2]
                if column_names == ["email"] and not is_unique:
                    has_email_non_unique_index = True
                    break
            assert has_email_non_unique_index
            usage_index_rows = (await session.execute(text("PRAGMA index_list(usage_history)"))).fetchall()
            usage_index_names = {str(row[1]) for row in usage_index_rows if len(row) > 1}
            assert "idx_usage_window_account_latest" in usage_index_names
            request_log_index_rows = (await session.execute(text("PRAGMA index_list(request_logs)"))).fetchall()
            request_log_index_names = {str(row[1]) for row in request_log_index_rows if len(row) > 1}
            assert "idx_logs_requested_at_id" in request_log_index_names

            await session.execute(
                text(
                    """
                    INSERT INTO accounts (
                        id, chatgpt_account_id, email, plan_type,
                        access_token_encrypted, refresh_token_encrypted, id_token_encrypted,
                        last_refresh, created_at, status, deactivation_reason, reset_at
                    )
                    VALUES (
                        'acc_legacy_2', 'chatgpt_legacy_2', 'legacy@example.com', 'team',
                        x'11', x'12', x'13',
                        '2026-01-01 00:00:00', '2026-01-01 00:00:00', 'active', NULL, NULL
                    )
                    """
                )
            )
            usage_count = (
                await session.execute(text("SELECT COUNT(*) FROM usage_history WHERE account_id='acc_legacy'"))
            ).scalar_one()
            logs_count = (
                await session.execute(text("SELECT COUNT(*) FROM request_logs WHERE account_id='acc_legacy'"))
            ).scalar_one()
            sticky_count = (
                await session.execute(text("SELECT COUNT(*) FROM sticky_sessions WHERE account_id='acc_legacy'"))
            ).scalar_one()
            await session.commit()

            assert usage_count == 1
            assert logs_count == 1
            assert sticky_count == 1
    finally:
        await engine.dispose()
