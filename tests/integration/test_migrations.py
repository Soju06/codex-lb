from __future__ import annotations

from collections.abc import Callable

import pytest
from anyio import to_thread
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.auth import DEFAULT_PLAN
from app.core.auth.dashboard_session_ttl import (
    DEFAULT_DASHBOARD_SESSION_TTL_SECONDS,
    REMOTE_DASHBOARD_SESSION_TTL_SECONDS,
)
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
    run_upgrade,
)
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository

try:
    from app.db.migrate import check_migration_policy as _check_migration_policy
except ImportError:
    check_migration_policy: Callable[[str], tuple[str, ...]] | None = None
else:
    check_migration_policy = _check_migration_policy
pytestmark = pytest.mark.integration
_DATABASE_URL = get_settings().database_url
_HEAD_REVISION = inspect_migration_state(_DATABASE_URL).head_revision
_STAMPED_AFTER_LEGACY_PREFIX_4 = OLD_TO_NEW_REVISION_MAP["004_add_accounts_chatgpt_account_id"]
_STAMPED_AFTER_LEGACY_PREFIX_1 = OLD_TO_NEW_REVISION_MAP["001_normalize_account_plan_types"]


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
async def test_run_startup_migrations_preserves_unknown_plan_types(db_setup):
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

    assert result.bootstrap.stamped_revision == _STAMPED_AFTER_LEGACY_PREFIX_4
    assert result.current_revision == _HEAD_REVISION

    async with SessionLocal() as session:
        revision_rows = await session.execute(text("SELECT version_num FROM alembic_version"))
        revisions = [str(row[0]) for row in revision_rows.fetchall()]
        assert revisions == [_HEAD_REVISION]


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
    assert result.current_revision == _HEAD_REVISION

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

    assert result.bootstrap.stamped_revision == _STAMPED_AFTER_LEGACY_PREFIX_1
    assert result.bootstrap.unknown_migrations == ("900_custom_hotfix",)
    assert result.current_revision == _HEAD_REVISION


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAS_REVISION_REMAP, reason="requires revision remap support")
async def test_run_startup_migrations_auto_remaps_legacy_alembic_revision_ids(db_setup):
    await run_startup_migrations(_DATABASE_URL)

    legacy_head = "013_add_dashboard_settings_routing_strategy"
    async with SessionLocal() as session:
        await session.execute(text("UPDATE alembic_version SET version_num = :legacy"), {"legacy": legacy_head})
        await session.commit()

    result = await run_startup_migrations(_DATABASE_URL)
    assert result.current_revision == _HEAD_REVISION

    async with SessionLocal() as session:
        revision_rows = await session.execute(text("SELECT version_num FROM alembic_version"))
        revisions = sorted(str(row[0]) for row in revision_rows.fetchall())
        assert revisions == [_HEAD_REVISION]


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAS_REVISION_REMAP, reason="requires revision remap support")
async def test_run_startup_migrations_auto_remaps_firewall_legacy_revision_id(db_setup):
    await run_startup_migrations(_DATABASE_URL)

    legacy_firewall_revision = "014_add_api_firewall_allowlist"
    async with SessionLocal() as session:
        await session.execute(
            text("UPDATE alembic_version SET version_num = :legacy"),
            {"legacy": legacy_firewall_revision},
        )
        await session.commit()

    result = await run_startup_migrations(_DATABASE_URL)
    assert result.current_revision == _HEAD_REVISION

    async with SessionLocal() as session:
        revision_rows = await session.execute(text("SELECT version_num FROM alembic_version"))
        revisions = sorted(str(row[0]) for row in revision_rows.fetchall())
        assert revisions == [_HEAD_REVISION]


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAS_REVISION_REMAP, reason="requires revision remap support")
async def test_run_startup_migrations_handles_legacy_schema_table_and_legacy_alembic_id_together(db_setup):
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
        await session.execute(
            text("UPDATE alembic_version SET version_num = :legacy"),
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
async def test_postgresql_migration_contract_policy_and_drift_match(db_setup):
    result = await run_startup_migrations(_DATABASE_URL)
    assert result.current_revision == _HEAD_REVISION

    assert check_migration_policy is not None
    assert check_migration_policy(_DATABASE_URL) == ()
    assert check_schema_drift(_DATABASE_URL) == ()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _is_postgresql_database_url(_DATABASE_URL),
    reason="PostgreSQL-only empty database migration test",
)
async def test_postgresql_upgrade_head_from_empty_database(db_setup):
    async with SessionLocal() as session:
        await session.execute(text("DROP SCHEMA public CASCADE"))
        await session.execute(text("CREATE SCHEMA public"))
        await session.commit()

    result = await run_startup_migrations(_DATABASE_URL)
    assert result.current_revision == _HEAD_REVISION

    async with SessionLocal() as session:
        revision_rows = await session.execute(text("SELECT version_num FROM alembic_version"))
        revisions = sorted(str(row[0]) for row in revision_rows.fetchall())
        assert revisions == [_HEAD_REVISION]


@pytest.mark.asyncio
@pytest.mark.skipif(
    (not _is_postgresql_database_url(_DATABASE_URL)) or (not _HAS_REVISION_REMAP),
    reason="PostgreSQL-only migration remap test",
)
async def test_postgresql_startup_migration_auto_remap_legacy_head(db_setup):
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
                        account_id VARCHAR NOT NULL,
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
                        error_message TEXT,
                        CONSTRAINT request_logs_account_id_fkey
                            FOREIGN KEY(account_id) REFERENCES accounts(id)
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
            dashboard_column_defaults = {str(row[1]): row[4] for row in dashboard_columns_rows if len(row) > 4}
            account_columns_rows = (await session.execute(text("PRAGMA table_info(accounts)"))).fetchall()
            account_columns = {str(row[1]) for row in account_columns_rows if len(row) > 1}
            api_key_columns_rows = (await session.execute(text("PRAGMA table_info(api_keys)"))).fetchall()
            api_key_columns = {str(row[1]) for row in api_key_columns_rows if len(row) > 1}
            api_key_column_defaults = {str(row[1]): row[4] for row in api_key_columns_rows if len(row) > 4}
            request_log_columns_rows = (await session.execute(text("PRAGMA table_info(request_logs)"))).fetchall()
            request_log_columns = {str(row[1]) for row in request_log_columns_rows if len(row) > 1}
            assert "deleted_at" in request_log_columns
            assert "transport" in request_log_columns
            assert "plan_type" in request_log_columns
            assert "source" in request_log_columns
            assert "archive_request_id" in request_log_columns
            assert "limit_warmup_enabled" in account_columns
            legacy_plan_type = (
                await session.execute(text("SELECT plan_type FROM request_logs WHERE id=1"))
            ).scalar_one()
            assert legacy_plan_type is None
            assert "limit_warmup_enabled" in dashboard_columns
            assert "limit_warmup_windows" in dashboard_columns
            assert "limit_warmup_model" in dashboard_columns
            assert "limit_warmup_prompt" in dashboard_columns
            assert "limit_warmup_cooldown_seconds" in dashboard_columns
            assert "limit_warmup_exhausted_threshold_percent" in dashboard_columns
            assert "limit_warmup_idle_threshold_percent" in dashboard_columns
            assert "limit_warmup_min_available_percent" in dashboard_columns
            exhausted_threshold = (
                await session.execute(
                    text("SELECT limit_warmup_exhausted_threshold_percent FROM dashboard_settings WHERE id=1")
                )
            ).scalar_one()
            assert exhausted_threshold == 99.0
            idle_threshold = (
                await session.execute(
                    text("SELECT limit_warmup_idle_threshold_percent FROM dashboard_settings WHERE id=1")
                )
            ).scalar_one()
            assert idle_threshold == 1.0
            assert "hide_upstream_quota_from_api_keys" in dashboard_columns
            assert dashboard_column_defaults["hide_upstream_quota_from_api_keys"] in ("0", 0, False)
            assert "single_account_id" in dashboard_columns
            assert "limit_warmup_staggered_idle_enabled" in dashboard_columns
            assert "usage_sections" in api_key_columns
            assert api_key_column_defaults["usage_sections"] in (
                "'upstream_limits,account_pool_usage'",
                '"upstream_limits,account_pool_usage"',
                "upstream_limits,account_pool_usage",
            )
            if "routing_strategy" in dashboard_columns:
                routing_strategy = (
                    await session.execute(text("SELECT routing_strategy FROM dashboard_settings WHERE id=1"))
                ).scalar_one()
                assert routing_strategy == "capacity_weighted"
            assert "relative_availability_power" in dashboard_columns
            relative_availability_power = (
                await session.execute(text("SELECT relative_availability_power FROM dashboard_settings WHERE id=1"))
            ).scalar_one()
            assert relative_availability_power == 2.0
            assert "relative_availability_top_k" in dashboard_columns
            relative_availability_top_k = (
                await session.execute(text("SELECT relative_availability_top_k FROM dashboard_settings WHERE id=1"))
            ).scalar_one()
            assert relative_availability_top_k == 5
            assert "openai_cache_affinity_max_age_seconds" in dashboard_columns
            affinity_ttl = (
                await session.execute(
                    text("SELECT openai_cache_affinity_max_age_seconds FROM dashboard_settings WHERE id=1")
                )
            ).scalar_one()
            assert affinity_ttl == 1800
            assert "http_responses_session_bridge_prompt_cache_idle_ttl_seconds" in dashboard_columns
            http_responses_ttl = (
                await session.execute(
                    text(
                        "SELECT http_responses_session_bridge_prompt_cache_idle_ttl_seconds"
                        " FROM dashboard_settings WHERE id=1"
                    )
                )
            ).scalar_one()
            assert http_responses_ttl == 3600
            assert "http_responses_session_bridge_gateway_safe_mode" in dashboard_columns
            gateway_safe_mode = (
                await session.execute(
                    text("SELECT http_responses_session_bridge_gateway_safe_mode FROM dashboard_settings WHERE id=1")
                )
            ).scalar_one()
            assert gateway_safe_mode in (False, 0)
            assert "sticky_reallocation_budget_threshold_pct" in dashboard_columns
            assert "sticky_reallocation_primary_budget_threshold_pct" in dashboard_columns
            assert "sticky_reallocation_secondary_budget_threshold_pct" in dashboard_columns
            sticky_budget_threshold = (
                await session.execute(
                    text("SELECT sticky_reallocation_budget_threshold_pct FROM dashboard_settings WHERE id=1")
                )
            ).scalar_one()
            assert sticky_budget_threshold == 95.0
            sticky_primary_threshold, sticky_secondary_threshold = (
                await session.execute(
                    text(
                        "SELECT sticky_reallocation_primary_budget_threshold_pct, "
                        "sticky_reallocation_secondary_budget_threshold_pct "
                        "FROM dashboard_settings WHERE id=1"
                    )
                )
            ).one()
            assert sticky_primary_threshold == 95.0
            assert sticky_secondary_threshold == 95.0
            sticky_columns_rows = (await session.execute(text("PRAGMA table_info(sticky_sessions)"))).fetchall()
            sticky_columns = {str(row[1]) for row in sticky_columns_rows if len(row) > 1}
            assert "kind" in sticky_columns
            sticky_kind = (
                await session.execute(text("SELECT kind FROM sticky_sessions WHERE key='sticky_1'"))
            ).scalar_one()
            assert sticky_kind == "sticky_thread"
            await session.execute(
                text(
                    """
                    INSERT INTO sticky_sessions (key, account_id, kind, created_at, updated_at)
                    VALUES ('sticky_1', 'acc_legacy', 'prompt_cache', '2026-01-01 00:00:00', '2026-01-01 00:00:00')
                    """
                )
            )
            sticky_same_key_count = (
                await session.execute(text("SELECT COUNT(*) FROM sticky_sessions WHERE key='sticky_1'"))
            ).scalar_one()
            assert sticky_same_key_count == 2
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
            assert "idx_usage_window_account_time" in usage_index_names
            request_log_index_rows = (await session.execute(text("PRAGMA index_list(request_logs)"))).fetchall()
            request_log_index_names = {str(row[1]) for row in request_log_index_rows if len(row) > 1}
            assert "idx_logs_requested_at_id" in request_log_index_names
            assert "idx_logs_deleted_at_requested_at_id" in request_log_index_names
            assert "idx_logs_requested_at_model_tier" in request_log_index_names
            assert "idx_logs_model_effort_time" in request_log_index_names
            assert "idx_logs_status_error_time" in request_log_index_names
            assert "idx_logs_api_key_time" in request_log_index_names
            assert "idx_logs_source_requested_at" in request_log_index_names
            warmup_table_exists = (
                await session.execute(
                    text("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='account_limit_warmups'")
                )
            ).scalar_one()
            assert warmup_table_exists == 1
            warmup_index_rows = (await session.execute(text("PRAGMA index_list(account_limit_warmups)"))).fetchall()
            warmup_index_names = {str(row[1]) for row in warmup_index_rows if len(row) > 1}
            assert "idx_account_limit_warmups_account_attempted" in warmup_index_names
            assert "idx_account_limit_warmups_status_attempted" in warmup_index_names
            request_log_fk_rows = (await session.execute(text("PRAGMA foreign_key_list(request_logs)"))).fetchall()
            request_log_fk_actions = {
                (str(row[2]).lower(), str(row[3]).lower(), str(row[4]).lower(), str(row[6]).lower())
                for row in request_log_fk_rows
                if len(row) > 6
            }
            assert ("accounts", "account_id", "id", "set null") in request_log_fk_actions
            api_key_index_rows = (await session.execute(text("PRAGMA index_list(api_keys)"))).fetchall()
            api_key_index_names = {str(row[1]) for row in api_key_index_rows if len(row) > 1}
            assert "idx_api_keys_name" in api_key_index_names

            await session.execute(
                text(
                    """
                    INSERT INTO accounts (
                        id, chatgpt_account_id, codex_installation_id, email, plan_type,
                        access_token_encrypted, refresh_token_encrypted, id_token_encrypted,
                        last_refresh, created_at, status, deactivation_reason, reset_at
                    )
                    VALUES (
                        'acc_legacy_2', 'chatgpt_legacy_2', 'legacy-installation-2', 'legacy@example.com', 'team',
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
            assert sticky_count == 2

            await session.execute(text("DELETE FROM usage_history WHERE account_id='acc_legacy'"))
            await session.execute(text("DELETE FROM sticky_sessions WHERE account_id='acc_legacy'"))
            await session.execute(text("DELETE FROM accounts WHERE id='acc_legacy'"))
            await session.commit()

            remaining_log = (
                await session.execute(text("SELECT account_id, deleted_at FROM request_logs WHERE id=1"))
            ).one()
            assert remaining_log[0] is None
            assert remaining_log[1] is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_dashboard_settings_default_flip_migration_does_not_infer_intent_from_updated_at(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'dashboard-settings-defaults.sqlite'}"
    base_revision = "20260408_010000_merge_import_without_overwrite_and_assignment_heads"

    await to_thread.run_sync(lambda: run_upgrade(db_url, base_revision, bootstrap_legacy=True))

    engine = create_async_engine(db_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            await session.execute(
                text(
                    """
                    UPDATE dashboard_settings
                    SET sticky_threads_enabled = 0,
                        prefer_earlier_reset_accounts = 0,
                        password_hash = 'bcrypt$demo',
                        updated_at = '2026-02-01 00:00:00'
                    WHERE id = 1
                    """
                )
            )
            await session.commit()

        await to_thread.run_sync(
            lambda: run_upgrade(
                db_url,
                "20260409_000000_switch_sticky_threads_and_prefer_earlier_reset_defaults_to_true",
                bootstrap_legacy=False,
            )
        )

        async with session_factory() as session:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT sticky_threads_enabled, prefer_earlier_reset_accounts
                        FROM dashboard_settings
                        WHERE id = 1
                        """
                    )
                )
            ).one()
            assert row[0] in (False, 0)
            assert row[1] in (False, 0)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_dashboard_settings_default_flip_migration_updates_fresh_seeded_row(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'dashboard-settings-defaults-fresh.sqlite'}"

    await to_thread.run_sync(
        lambda: run_upgrade(
            db_url,
            "20260409_000000_switch_sticky_threads_and_prefer_earlier_reset_defaults_to_true",
            bootstrap_legacy=True,
        )
    )

    engine = create_async_engine(db_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT sticky_threads_enabled, prefer_earlier_reset_accounts
                        FROM dashboard_settings
                        WHERE id = 1
                        """
                    )
                )
            ).one()
            assert row[0] in (True, 1)
            assert row[1] in (True, 1)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_dashboard_settings_default_flip_migration_updates_pristine_fresh_db_upgraded_in_steps(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'dashboard-settings-defaults-staged-fresh.sqlite'}"

    await to_thread.run_sync(
        lambda: run_upgrade(
            db_url,
            "20260408_010000_merge_import_without_overwrite_and_assignment_heads",
            bootstrap_legacy=True,
        )
    )

    await to_thread.run_sync(lambda: run_upgrade(db_url, "head", bootstrap_legacy=False))

    engine = create_async_engine(db_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT sticky_threads_enabled, prefer_earlier_reset_accounts
                        FROM dashboard_settings
                        WHERE id = 1
                        """
                    )
                )
            ).one()
            assert row[0] in (True, 1)
            assert row[1] in (True, 1)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("initial_ttl_seconds", "expected_ttl_seconds"),
    [
        (REMOTE_DASHBOARD_SESSION_TTL_SECONDS, DEFAULT_DASHBOARD_SESSION_TTL_SECONDS),
        (7200, 7200),
    ],
)
async def test_dashboard_session_ttl_migration_updates_only_legacy_default(
    tmp_path,
    initial_ttl_seconds: int,
    expected_ttl_seconds: int,
):
    db_url = f"sqlite+aiosqlite:///{tmp_path / f'dashboard-session-ttl-{initial_ttl_seconds}.sqlite'}"
    parent_revision = "20260701_000000_add_weekly_pace_smoothing_minutes"
    target_revision = "20260705_000000_harden_dashboard_session_ttl"

    await to_thread.run_sync(lambda: run_upgrade(db_url, parent_revision, bootstrap_legacy=True))

    engine = create_async_engine(db_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            await session.execute(
                text(
                    """
                    UPDATE dashboard_settings
                    SET dashboard_session_ttl_seconds = :initial_ttl_seconds
                    WHERE id = 1
                    """
                ),
                {"initial_ttl_seconds": initial_ttl_seconds},
            )
            await session.commit()

        await to_thread.run_sync(lambda: run_upgrade(db_url, target_revision, bootstrap_legacy=False))

        async with session_factory() as session:
            ttl_seconds = (
                await session.execute(
                    text(
                        """
                        SELECT dashboard_session_ttl_seconds
                        FROM dashboard_settings
                        WHERE id = 1
                        """
                    )
                )
            ).scalar_one()
            assert ttl_seconds == expected_ttl_seconds
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_free_account_monthly_migration_renames_only_free_usage_windows(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'free-monthly-migration.sqlite'}"

    await to_thread.run_sync(
        lambda: run_upgrade(
            db_url,
            "20260604_000000_add_reauth_required_account_status",
            bootstrap_legacy=True,
        )
    )

    engine = create_async_engine(db_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO accounts (
                        id, email, plan_type,
                        access_token_encrypted, refresh_token_encrypted, id_token_encrypted,
                        last_refresh, status
                    )
                    VALUES
                      ('acc_free_monthly_migration', 'free-monthly@example.com', 'free',
                       x'01', x'02', x'03', '2026-01-01 00:00:00', 'active'),
                      ('acc_paid_monthly_migration', 'paid-monthly@example.com', 'plus',
                       x'04', x'05', x'06', '2026-01-01 00:00:00', 'active')
                    """
                )
            )
            await session.execute(
                text(
                    """
                    INSERT INTO usage_history (account_id, recorded_at, window, used_percent)
                    VALUES
                      ('acc_free_monthly_migration', CURRENT_TIMESTAMP, 'primary', 10.0),
                      ('acc_free_monthly_migration', CURRENT_TIMESTAMP, 'secondary', 20.0),
                      ('acc_free_monthly_migration', CURRENT_TIMESTAMP, NULL, 25.0),
                      ('acc_paid_monthly_migration', CURRENT_TIMESTAMP, 'primary', 30.0),
                      ('acc_paid_monthly_migration', CURRENT_TIMESTAMP, 'secondary', 40.0),
                      ('acc_paid_monthly_migration', CURRENT_TIMESTAMP, NULL, 45.0)
                    """
                )
            )
            await session.commit()

        await to_thread.run_sync(lambda: run_upgrade(db_url, "head", bootstrap_legacy=False))

        async with session_factory() as session:
            free_windows = [
                row[0]
                for row in (
                    await session.execute(
                        text("SELECT window FROM usage_history WHERE account_id = :account_id ORDER BY id"),
                        {"account_id": "acc_free_monthly_migration"},
                    )
                ).all()
            ]
            paid_windows = [
                row[0]
                for row in (
                    await session.execute(
                        text("SELECT window FROM usage_history WHERE account_id = :account_id ORDER BY id"),
                        {"account_id": "acc_paid_monthly_migration"},
                    )
                ).all()
            ]
    finally:
        await engine.dispose()

    assert free_windows == ["old-primary", "old-secondary", "old-primary"]
    assert paid_windows == ["primary", "secondary", None]


@pytest.mark.asyncio
async def test_reset_credit_redeem_tables_migration_upgrade_and_downgrade(tmp_path):
    from alembic import command as alembic_command

    from app.db.migrate import _build_alembic_config

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'reset-credit-redeem-tables.sqlite'}"
    revision = "20260713_070000_add_reset_credit_redeem_tables"
    parent_revision = "20260712_020000_add_api_key_usage_rollups"

    await to_thread.run_sync(lambda: run_upgrade(db_url, revision, bootstrap_legacy=True))

    async def _table_names() -> set[str]:
        engine = create_async_engine(db_url, future=True)
        try:
            async with engine.connect() as conn:
                rows = await conn.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'"))
                return {row[0] for row in rows}
        finally:
            await engine.dispose()

    upgraded_tables = await _table_names()
    assert "reset_credit_redeem_requests" in upgraded_tables
    assert "reset_credit_redeem_claims" in upgraded_tables

    await to_thread.run_sync(lambda: alembic_command.downgrade(_build_alembic_config(db_url), parent_revision))

    downgraded_tables = await _table_names()
    assert "reset_credit_redeem_requests" not in downgraded_tables
    assert "reset_credit_redeem_claims" not in downgraded_tables

    # Upgrading again after the downgrade must succeed (round-trip safety).
    await to_thread.run_sync(lambda: run_upgrade(db_url, revision, bootstrap_legacy=False))
    assert "reset_credit_redeem_requests" in await _table_names()


@pytest.mark.asyncio
async def test_model_registry_snapshot_migration_upgrade_and_downgrade(tmp_path):
    from alembic import command
    from sqlalchemy import inspect as sa_inspect

    from app.db.migrate import _build_alembic_config

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'model-registry-snapshot.sqlite'}"
    parent_revision = "20260712_020000_add_api_key_usage_rollups"

    def _table_state(sync_conn):
        inspector = sa_inspect(sync_conn)
        if not inspector.has_table("model_registry_snapshot"):
            return None
        return {column["name"] for column in inspector.get_columns("model_registry_snapshot")}

    await to_thread.run_sync(lambda: run_upgrade(db_url, "head", bootstrap_legacy=False))
    engine = create_async_engine(db_url)
    try:
        async with engine.connect() as conn:
            columns = await conn.run_sync(_table_state)
        assert columns == {"id", "schema_version", "content_hash", "payload", "refreshed_at", "leader_id"}

        await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(db_url), parent_revision))
        async with engine.connect() as conn:
            assert await conn.run_sync(_table_state) is None

        result = await to_thread.run_sync(lambda: run_upgrade(db_url, "head", bootstrap_legacy=False))
        assert result.current_revision == _HEAD_REVISION
        async with engine.connect() as conn:
            assert await conn.run_sync(_table_state) is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_account_refresh_claims_migration_upgrade_and_downgrade(tmp_path):
    """Upgrade creates the refresh-claim coordination table; downgrade drops it;
    a final walk to head proves the revision sits on a single-head graph."""
    from alembic import command

    from app.db.migrate import _build_alembic_config

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'refresh-claims.sqlite'}"
    parent_revision = "20260713_020000_add_model_registry_snapshot"
    claim_revision = "20260713_040000_add_account_refresh_claims"

    async def _has_claims_table(engine) -> bool:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'account_refresh_claims'")
            )
            return result.scalar_one_or_none() is not None

    await to_thread.run_sync(lambda: run_upgrade(db_url, parent_revision, bootstrap_legacy=False))
    engine = create_async_engine(db_url, future=True)
    try:
        assert not await _has_claims_table(engine)

        await to_thread.run_sync(lambda: run_upgrade(db_url, claim_revision, bootstrap_legacy=False))
        assert await _has_claims_table(engine)

        config = _build_alembic_config(db_url)
        await to_thread.run_sync(lambda: command.downgrade(config, parent_revision))
        assert not await _has_claims_table(engine)

        # Single-head sanity: upgrading to "head" from the parent must pass
        # through the claim revision without a multi-head failure.
        result = await to_thread.run_sync(lambda: run_upgrade(db_url, "head", bootstrap_legacy=False))
        assert result.current_revision == _HEAD_REVISION
        assert await _has_claims_table(engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_oauth_flow_states_migration_upgrade_and_downgrade(tmp_path):
    """Upgrade creates the OAuth flow-state coordination table; downgrade drops
    it; a final walk to head proves the revision sits on a single-head graph."""
    from alembic import command

    from app.db.migrate import _build_alembic_config

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'oauth-flow-states.sqlite'}"
    parent_revision = "20260713_040000_add_account_refresh_claims"
    flow_revision = "20260714_000000_add_oauth_flow_states"

    async def _has_flow_table(engine) -> bool:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'oauth_flow_states'")
            )
            return result.scalar_one_or_none() is not None

    await to_thread.run_sync(lambda: run_upgrade(db_url, parent_revision, bootstrap_legacy=False))
    engine = create_async_engine(db_url, future=True)
    try:
        assert not await _has_flow_table(engine)

        await to_thread.run_sync(lambda: run_upgrade(db_url, flow_revision, bootstrap_legacy=False))
        assert await _has_flow_table(engine)

        config = _build_alembic_config(db_url)
        await to_thread.run_sync(lambda: command.downgrade(config, parent_revision))
        assert not await _has_flow_table(engine)

        # Single-head sanity: upgrading to "head" from the parent must pass
        # through the flow revision without a multi-head failure.
        result = await to_thread.run_sync(lambda: run_upgrade(db_url, "head", bootstrap_legacy=False))
        assert result.current_revision == _HEAD_REVISION
        assert await _has_flow_table(engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_dashboard_retention_settings_migration_upgrade_and_downgrade(tmp_path):
    """Upgrade adds the nullable dashboard retention columns; downgrade drops
    them; a final walk to head proves the revision sits on a single-head graph."""
    from alembic import command

    from app.db.migrate import _build_alembic_config

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'dashboard-retention.sqlite'}"
    parent_revision = "20260716_000000_add_oauth_device_flow_slots"
    retention_revision = "20260716_010000_add_dashboard_retention_settings"
    column_names = ("request_log_retention_days", "usage_history_retention_days")

    async def _dashboard_columns(engine) -> set[str]:
        async with engine.connect() as conn:
            rows = await conn.execute(text("PRAGMA table_info('dashboard_settings')"))
            return {row[1] for row in rows}

    await to_thread.run_sync(lambda: run_upgrade(db_url, parent_revision, bootstrap_legacy=False))
    engine = create_async_engine(db_url, future=True)
    try:
        assert not set(column_names) & await _dashboard_columns(engine)

        await to_thread.run_sync(lambda: run_upgrade(db_url, retention_revision, bootstrap_legacy=False))
        upgraded_columns = await _dashboard_columns(engine)
        assert set(column_names) <= upgraded_columns

        # The pre-existing (seeded) row keeps NULL (no dashboard override):
        # env aliases continue to apply, so historical deployments need no
        # backfill.
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text("SELECT request_log_retention_days, usage_history_retention_days FROM dashboard_settings")
                )
            ).all()
            assert rows
            assert all(row == (None, None) for row in rows)

        config = _build_alembic_config(db_url)
        await to_thread.run_sync(lambda: command.downgrade(config, parent_revision))
        assert not set(column_names) & await _dashboard_columns(engine)

        # Single-head sanity: upgrading to "head" from the parent must pass
        # through the retention revision without a multi-head failure.
        result = await to_thread.run_sync(lambda: run_upgrade(db_url, "head", bootstrap_legacy=False))
        assert result.current_revision == _HEAD_REVISION
        assert set(column_names) <= await _dashboard_columns(engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_request_log_conversation_id_migration_upgrade_and_downgrade(tmp_path):
    from alembic import command

    from app.db.migrate import _build_alembic_config

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'request-log-conversation-id.sqlite'}"
    parent_revision = "20260717_000000_optimize_dashboard_hot_path_indexes"
    conversation_revision = "20260720_000000_add_request_log_conversation_id"

    async def _request_log_schema(engine) -> tuple[set[str], set[str]]:
        async with engine.connect() as conn:
            columns = {row[1] for row in await conn.execute(text("PRAGMA table_info('request_logs')"))}
            indexes = {row[1] for row in await conn.execute(text("PRAGMA index_list('request_logs')"))}
            return columns, indexes

    await to_thread.run_sync(lambda: run_upgrade(db_url, parent_revision, bootstrap_legacy=False))
    engine = create_async_engine(db_url, future=True)
    try:
        columns, indexes = await _request_log_schema(engine)
        assert "conversation_id" not in columns
        assert "idx_logs_conversation_id" not in indexes

        await to_thread.run_sync(lambda: run_upgrade(db_url, conversation_revision, bootstrap_legacy=False))
        columns, indexes = await _request_log_schema(engine)
        assert "conversation_id" in columns
        assert "idx_logs_conversation_id" in indexes

        await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(db_url), parent_revision))
        columns, indexes = await _request_log_schema(engine)
        assert "idx_logs_conversation_id" not in indexes
        assert "conversation_id" not in columns
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_usage_history_bulk_covering_indexes_migration_upgrade_and_downgrade(tmp_path):
    from alembic import command

    from app.db.migrate import _build_alembic_config

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'usage-history-bulk-covering.sqlite'}"
    parent_revision = "20260730_000000_add_api_key_fair_share_threshold"
    covering_revision = "20260806_020000_add_usage_history_bulk_covering_indexes"
    covering_indexes = {
        "idx_usage_window_account_time_covering",
        "idx_usage_window_raw_account_time_covering",
    }

    async def _usage_history_indexes(engine) -> set[str]:
        async with engine.connect() as conn:
            return {row[1] for row in await conn.execute(text("PRAGMA index_list('usage_history')"))}

    await to_thread.run_sync(lambda: run_upgrade(db_url, parent_revision, bootstrap_legacy=False))
    engine = create_async_engine(db_url, future=True)
    try:
        indexes = await _usage_history_indexes(engine)
        assert not (covering_indexes & indexes)

        await to_thread.run_sync(lambda: run_upgrade(db_url, covering_revision, bootstrap_legacy=False))
        indexes = await _usage_history_indexes(engine)
        assert covering_indexes <= indexes

        await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(db_url), parent_revision))
        indexes = await _usage_history_indexes(engine)
        assert not (covering_indexes & indexes)

        # Idempotent re-upgrade (IF NOT EXISTS tolerates pre-created indexes).
        await to_thread.run_sync(lambda: run_upgrade(db_url, covering_revision, bootstrap_legacy=False))
        indexes = await _usage_history_indexes(engine)
        assert covering_indexes <= indexes
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _is_postgresql_database_url(_DATABASE_URL),
    reason="PostgreSQL-only invalid covering-index repair test",
)
async def test_usage_history_covering_index_migration_repairs_invalid_leftover_postgresql(db_setup):
    """An invalid leftover from an interrupted CREATE INDEX CONCURRENTLY is rebuilt.

    ``IF NOT EXISTS`` alone would silently accept an invalid same-named index,
    leaving the bulk fetch without a usable covering index. Pins the repair
    probe: step the schema back below the covering revision, plant a decoy
    key-only index marked invalid (exactly what an interrupted concurrent
    build leaves behind), and assert the re-applied migration replaces it
    with a valid covering index.
    """
    from alembic import command

    from app.db.migrate import _build_alembic_config

    parent_revision = "20260730_000000_add_api_key_fair_share_threshold"
    index_name = "idx_usage_window_account_time_covering"

    await run_startup_migrations(_DATABASE_URL)
    await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(_DATABASE_URL), parent_revision))

    async with SessionLocal() as session:
        await session.execute(text(f"CREATE INDEX {index_name} ON usage_history (account_id)"))
        await session.execute(
            text("UPDATE pg_index SET indisvalid = false WHERE indexrelid = CAST(:name AS regclass)"),
            {"name": index_name},
        )
        await session.commit()

    result = await run_startup_migrations(_DATABASE_URL)
    assert result.current_revision == _HEAD_REVISION

    async with SessionLocal() as session:
        indisvalid = (
            await session.execute(
                text(
                    "SELECT i.indisvalid FROM pg_index i "
                    "JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :name"
                ),
                {"name": index_name},
            )
        ).scalar_one()
        indexdef = (
            await session.execute(
                text("SELECT pg_get_indexdef(CAST(:name AS regclass))"),
                {"name": index_name},
            )
        ).scalar_one()

    assert indisvalid is True
    assert "INCLUDE" in indexdef  # rebuilt as the covering index, not the accepted decoy


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _is_postgresql_database_url(_DATABASE_URL),
    reason="PostgreSQL-only autovacuum reloptions test",
)
async def test_usage_history_autovacuum_tuning_migration_sets_and_resets_reloptions_postgresql(db_setup):
    """The autovacuum tuning revision round-trips and tolerates manual pre-application.

    ``usage_history`` is append-heavy, so a stale visibility map silently
    degrades the covering indexes' index-only scans into per-row heap
    fetches. Pins that the migration sets the insert-driven autovacuum
    parameters, that downgrade resets them, and that re-applying over a
    deployment that already carries the identical manual ``ALTER TABLE``
    (the reference deployment's hotfix) is harmless.
    """
    from alembic import command

    from app.db.migrate import _build_alembic_config

    parent_revision = "20260806_020000_add_usage_history_bulk_covering_indexes"
    expected_options = {
        "autovacuum_vacuum_insert_scale_factor=0.02",
        "autovacuum_vacuum_insert_threshold=50000",
        "autovacuum_analyze_scale_factor=0.02",
    }

    async def _usage_history_reloptions() -> set[str]:
        async with SessionLocal() as session:
            options = (
                await session.execute(text("SELECT reloptions FROM pg_class WHERE relname = 'usage_history'"))
            ).scalar_one()
            return set(options or ())

    result = await run_startup_migrations(_DATABASE_URL)
    assert result.current_revision == _HEAD_REVISION
    assert expected_options <= await _usage_history_reloptions()

    await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(_DATABASE_URL), parent_revision))
    assert not (expected_options & await _usage_history_reloptions())

    # Simulate the reference deployment's manual hotfix, then re-apply the
    # migration on top of it: ALTER TABLE ... SET is idempotent.
    async with SessionLocal() as session:
        await session.execute(
            text(
                "ALTER TABLE usage_history SET ("
                "autovacuum_vacuum_insert_scale_factor = 0.02, "
                "autovacuum_vacuum_insert_threshold = 50000, "
                "autovacuum_analyze_scale_factor = 0.02)"
            )
        )
        await session.commit()

    rerun = await run_startup_migrations(_DATABASE_URL)
    assert rerun.current_revision == _HEAD_REVISION
    assert expected_options <= await _usage_history_reloptions()


@pytest.mark.asyncio
async def test_account_pending_deletion_migration_upgrade_and_downgrade(tmp_path):
    """Round-trip the pending-deletion marker migration through Alembic:
    parent -> revision adds the two guarded marker columns and the partial
    queue index, downgrade removes all three, the guarded upgrade tolerates
    pre-existing columns, and an upgrade to head proves the revision sits on
    the single-head path."""
    from alembic import command
    from sqlalchemy import inspect as sa_inspect

    from app.db.migrate import _build_alembic_config

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'account-pending-deletion.sqlite'}"
    parent_revision = "20260812_120000_add_sticky_abandonment_scope"
    pending_deletion_revision = "20260816_000000_add_account_pending_deletion"
    marker_columns = {"delete_requested_at", "delete_history_requested"}
    index_name = "idx_accounts_delete_requested_at"

    def _schema_state(sync_conn):
        inspector = sa_inspect(sync_conn)
        columns = {column["name"] for column in inspector.get_columns("accounts")}
        indexes = {index["name"] for index in inspector.get_indexes("accounts")}
        return {"columns": columns & marker_columns, "index_present": index_name in indexes}

    await to_thread.run_sync(lambda: run_upgrade(db_url, parent_revision, bootstrap_legacy=False))
    engine = create_async_engine(db_url, future=True)
    try:
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
        assert state == {"columns": set(), "index_present": False}

        await to_thread.run_sync(lambda: run_upgrade(db_url, pending_deletion_revision, bootstrap_legacy=False))
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
        assert state == {"columns": marker_columns, "index_present": True}

        # Downgrade refuses while a deletion is queued: the marker columns are
        # the queue's only durable state, and dropping them would silently
        # abandon an acknowledged deletion.
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO accounts (id, codex_installation_id, email, plan_type, "
                    "access_token_encrypted, refresh_token_encrypted, id_token_encrypted, "
                    "last_refresh, status, delete_requested_at, delete_history_requested) "
                    "VALUES ('acc_mig_pending', 'install-mig-pending', 'mig@example.com', 'plus', "
                    "X'00', X'00', X'00', '2026-08-16 00:00:00', 'deactivated', "
                    "'2026-08-16 00:00:00', 0)"
                )
            )
        with pytest.raises(Exception, match="queued for"):
            await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(db_url), parent_revision))
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
        assert state == {"columns": marker_columns, "index_present": True}
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM accounts WHERE id = 'acc_mig_pending'"))

        await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(db_url), parent_revision))
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
        assert state == {"columns": set(), "index_present": False}

        # Guarded upgrade: a database where the columns already exist (e.g. a
        # pre-merge build of this revision) must upgrade cleanly and still
        # create the missing index.
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE accounts ADD COLUMN delete_requested_at DATETIME"))
        await to_thread.run_sync(lambda: run_upgrade(db_url, pending_deletion_revision, bootstrap_legacy=False))
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
        assert state == {"columns": marker_columns, "index_present": True}

        # Single-head path: upgrading to head from here must succeed and keep
        # the marker schema in place.
        await to_thread.run_sync(lambda: run_upgrade(db_url, "head", bootstrap_legacy=False))
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
        assert state == {"columns": marker_columns, "index_present": True}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_account_plan_downgrade_observations_migration_upgrade_and_downgrade(tmp_path):
    """Round-trip the plan-downgrade evidence migration through Alembic itself.

    The store tests build their schema through ``Base.metadata.create_all``, so
    only this test executes the revision's ``upgrade`` and ``downgrade``
    functions: parent -> revision creates the table with the expected shape,
    downgrade removes it, the guarded upgrade tolerates a database where a
    pre-merge build of this same revision already created the table, and a
    final upgrade to ``head`` proves the re-parented revision sits on the
    single-head path.
    """
    from alembic import command
    from sqlalchemy import inspect as sa_inspect

    from app.db.migrate import _build_alembic_config

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'plan-downgrade-observations.sqlite'}"
    parent_revision = "20260803_000000_merge_http_bridge_recovery_and_capability_lineage_heads"
    observations_revision = "20260726_000000_add_account_plan_downgrade_observations"
    table_name = "account_plan_downgrade_observations"
    expected_columns = {
        "account_id",
        "observations",
        "credential_fingerprint",
        "observed_plan_type",
        "first_observed_at",
        "last_observed_at",
    }

    def _schema_state(sync_conn):
        inspector = sa_inspect(sync_conn)
        present = inspector.has_table(table_name)
        return {
            "present": present,
            "columns": ({column["name"] for column in inspector.get_columns(table_name)} if present else set()),
            "pk": (inspector.get_pk_constraint(table_name)["constrained_columns"] if present else None),
        }

    await to_thread.run_sync(lambda: run_upgrade(db_url, parent_revision, bootstrap_legacy=False))
    engine = create_async_engine(db_url, future=True)
    try:
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
        assert not state["present"]

        await to_thread.run_sync(lambda: run_upgrade(db_url, observations_revision, bootstrap_legacy=False))
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
        assert state["present"]
        assert state["columns"] == expected_columns
        assert state["pk"] == ["account_id"]

        await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(db_url), parent_revision))
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
        assert not state["present"]

        # Guarded upgrade: a database where a pre-merge build of this same
        # revision already created the table must upgrade cleanly without a
        # second CREATE TABLE.
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE account_plan_downgrade_observations ("
                    "account_id VARCHAR NOT NULL, observations INTEGER NOT NULL, "
                    "credential_fingerprint VARCHAR(64) NOT NULL, observed_plan_type VARCHAR NOT NULL, "
                    "first_observed_at DATETIME NOT NULL, last_observed_at DATETIME NOT NULL, "
                    "PRIMARY KEY (account_id))"
                )
            )
        await to_thread.run_sync(lambda: run_upgrade(db_url, observations_revision, bootstrap_legacy=False))
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
        assert state["present"]
        assert state["columns"] == expected_columns

        # The revision must sit on the single-head path: upgrading to head
        # from here succeeds without a divergent-heads error and leaves the
        # table in place.
        await to_thread.run_sync(lambda: run_upgrade(db_url, "head", bootstrap_legacy=False))
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
        assert state["present"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_request_usage_time_rollups_migration_upgrade_and_downgrade(tmp_path):
    from alembic import command
    from sqlalchemy import inspect as sa_inspect

    from app.db.migrate import _build_alembic_config

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'request-usage-time-rollups.sqlite'}"
    parent_revision = "20260722_000000_backfill_request_log_useragent_families"
    rollups_revision = "20260724_000000_add_request_usage_time_rollups"
    rollup_tables = (
        "request_usage_hourly_rollups",
        "request_usage_hourly_error_rollups",
        "request_demand_quarter_rollups",
    )

    def _schema_state(sync_conn):
        inspector = sa_inspect(sync_conn)
        return {
            "tables": {name for name in rollup_tables if inspector.has_table(name)},
            "state_columns": {column["name"] for column in inspector.get_columns("account_usage_rollup_state")},
            "hourly_pk": (
                inspector.get_pk_constraint("request_usage_hourly_rollups")["constrained_columns"]
                if inspector.has_table("request_usage_hourly_rollups")
                else None
            ),
            "quarter_pk": (
                inspector.get_pk_constraint("request_demand_quarter_rollups")["constrained_columns"]
                if inspector.has_table("request_demand_quarter_rollups")
                else None
            ),
        }

    await to_thread.run_sync(lambda: run_upgrade(db_url, parent_revision, bootstrap_legacy=False))
    engine = create_async_engine(db_url, future=True)
    try:
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
        assert state["tables"] == set()
        assert "hourly_folded_through" not in state["state_columns"]

        await to_thread.run_sync(lambda: run_upgrade(db_url, rollups_revision, bootstrap_legacy=False))
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
        assert state["tables"] == set(rollup_tables)
        assert "hourly_folded_through" in state["state_columns"]
        assert state["hourly_pk"] == [
            "bucket_epoch",
            "account_id",
            "api_key_id",
            "model",
            "service_tier",
            "request_kind",
            "is_deleted",
        ]
        quarter_pk = [
            "slot_epoch",
            "account_id",
            "api_key_id",
            "model",
            "reasoning_effort",
            "request_kind",
            "status",
            "is_deleted",
        ]
        assert state["quarter_pk"] == quarter_pk

        # The migration-seeded fold-state row (older revision) must have been
        # backfilled to the epoch by the new column's server default, without
        # touching the lifetime watermark.
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT folded_through, hourly_folded_through FROM account_usage_rollup_state WHERE id = 1")
                )
            ).one()
        assert str(row[1]).startswith("1970-01-01")

        await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(db_url), parent_revision))
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
        assert state["tables"] == set()
        assert "hourly_folded_through" not in state["state_columns"]

        # Guarded upgrade against the PRE-MERGE quarter shape (an unreleased
        # revision of this same migration created it without the fine-grain
        # planner dimensions): the upgrade must rebuild the table to the new
        # shape and reset the hourly watermark so the fold repopulates it.
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE request_demand_quarter_rollups ("
                    "slot_epoch BIGINT NOT NULL, account_id VARCHAR NOT NULL DEFAULT '', "
                    "request_kind VARCHAR NOT NULL, is_deleted BOOLEAN NOT NULL DEFAULT 0, "
                    "request_count BIGINT NOT NULL DEFAULT 0, input_tokens BIGINT NOT NULL DEFAULT 0, "
                    "output_or_reasoning_tokens BIGINT NOT NULL DEFAULT 0, "
                    "cached_input_tokens BIGINT NOT NULL DEFAULT 0, cost_usd FLOAT NOT NULL DEFAULT 0, "
                    "PRIMARY KEY (slot_epoch, account_id, request_kind, is_deleted))"
                )
            )
        await to_thread.run_sync(lambda: run_upgrade(db_url, rollups_revision, bootstrap_legacy=False))
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
        assert state["tables"] == set(rollup_tables)
        assert "hourly_folded_through" in state["state_columns"]
        assert state["quarter_pk"] == quarter_pk

        # Guarded upgrade against the PRE-MERGE ''-sentinel ENCODING (an
        # unreleased revision declared DEFAULT '' on the dimension columns
        # and stored NULL dimensions as '', colliding with legitimate
        # empty-string values): the upgrade must rebuild the tables empty and
        # reset the hourly watermark so the fold re-encodes from raw.
        await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(db_url), parent_revision))
        # (Fresh connect between the thread-side alembic op and the next
        # async DDL, matching the other legs: asserts the downgrade landed
        # and refreshes the pooled connection's schema snapshot.)
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
        assert state["tables"] == set()
        assert "hourly_folded_through" not in state["state_columns"]
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "ALTER TABLE account_usage_rollup_state ADD COLUMN hourly_folded_through DATETIME "
                    "NOT NULL DEFAULT '2025-07-01 00:00:00'"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE request_demand_quarter_rollups ("
                    "slot_epoch BIGINT NOT NULL, account_id VARCHAR NOT NULL DEFAULT '', "
                    "api_key_id VARCHAR NOT NULL DEFAULT '', model VARCHAR NOT NULL, "
                    "reasoning_effort VARCHAR NOT NULL DEFAULT '', request_kind VARCHAR NOT NULL, "
                    "status VARCHAR NOT NULL, is_deleted BOOLEAN NOT NULL DEFAULT 0, "
                    "request_count BIGINT NOT NULL DEFAULT 0, input_tokens BIGINT NOT NULL DEFAULT 0, "
                    "output_or_reasoning_tokens BIGINT NOT NULL DEFAULT 0, "
                    "cached_input_tokens BIGINT NOT NULL DEFAULT 0, cost_usd FLOAT NOT NULL DEFAULT 0, "
                    "PRIMARY KEY (slot_epoch, account_id, api_key_id, model, reasoning_effort, request_kind, "
                    "status, is_deleted))"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO request_demand_quarter_rollups "
                    "(slot_epoch, account_id, api_key_id, model, reasoning_effort, request_kind, status, is_deleted, "
                    "request_count) VALUES (900, 'acc', '', 'gpt-5.1-codex', '', 'normal', 'success', 0, 3)"
                )
            )
        await to_thread.run_sync(lambda: run_upgrade(db_url, rollups_revision, bootstrap_legacy=False))
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
            stale_encoded = (
                await conn.execute(text("SELECT COUNT(*) FROM request_demand_quarter_rollups"))
            ).scalar_one()
            watermark = (
                await conn.execute(text("SELECT hourly_folded_through FROM account_usage_rollup_state WHERE id = 1"))
            ).scalar_one()
        assert state["tables"] == set(rollup_tables)
        assert state["quarter_pk"] == quarter_pk
        assert stale_encoded == 0
        assert str(watermark).startswith("1970-01-01")

        # Guarded upgrade with the CURRENT shape already present (no
        # dimension-column defaults): inspector guards must skip (not
        # rebuild) existing objects, preserving table contents.
        await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(db_url), parent_revision))
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE request_demand_quarter_rollups ("
                    "slot_epoch BIGINT NOT NULL, account_id VARCHAR NOT NULL, "
                    "api_key_id VARCHAR NOT NULL, model VARCHAR NOT NULL, "
                    "reasoning_effort VARCHAR NOT NULL, request_kind VARCHAR NOT NULL, "
                    "status VARCHAR NOT NULL, is_deleted BOOLEAN NOT NULL DEFAULT 0, "
                    "request_count BIGINT NOT NULL DEFAULT 0, input_tokens BIGINT NOT NULL DEFAULT 0, "
                    "output_or_reasoning_tokens BIGINT NOT NULL DEFAULT 0, "
                    "cached_input_tokens BIGINT NOT NULL DEFAULT 0, cost_usd FLOAT NOT NULL DEFAULT 0, "
                    "PRIMARY KEY (slot_epoch, account_id, api_key_id, model, reasoning_effort, request_kind, "
                    "status, is_deleted))"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO request_demand_quarter_rollups "
                    "(slot_epoch, account_id, api_key_id, model, reasoning_effort, request_kind, status, is_deleted, "
                    "request_count) VALUES (900, 'acc', '', 'gpt-5.1-codex', '', 'normal', 'success', 0, 3)"
                )
            )
        await to_thread.run_sync(lambda: run_upgrade(db_url, rollups_revision, bootstrap_legacy=False))
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
            survivor = (
                await conn.execute(text("SELECT request_count FROM request_demand_quarter_rollups"))
            ).scalar_one()
        assert state["tables"] == set(rollup_tables)
        assert state["quarter_pk"] == quarter_pk
        assert survivor == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stamped_merge_rollup_repair_downgrade_preserves_schema(tmp_path):
    from alembic import command
    from sqlalchemy import inspect as sa_inspect

    from app.db.migrate import _build_alembic_config

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'stamped-merge-rollup-repair.sqlite'}"
    merge_revision = "20260724_000000_merge_request_log_schema_heads"
    final_revision = "20260728_000000_merge_pending_tool_calls_and_rollup_repair_heads"
    rollup_tables = {
        "request_usage_hourly_rollups",
        "request_usage_hourly_error_rollups",
        "request_demand_quarter_rollups",
    }

    await to_thread.run_sync(lambda: command.stamp(_build_alembic_config(db_url), merge_revision))
    # The stamped deployment path already has this durable bridge table.  Keep
    # the fixture representative so the pending-tool-call migration can alter
    # it just as it does on a real deployed database.
    engine = create_async_engine(db_url, future=True)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    CREATE TABLE http_bridge_sessions (
                        id VARCHAR(36) PRIMARY KEY,
                        session_key_kind VARCHAR(64) NOT NULL,
                        session_key_value TEXT NOT NULL,
                        session_key_hash VARCHAR(64) NOT NULL,
                        api_key_scope VARCHAR(255) NOT NULL,
                        owner_instance_id VARCHAR(255),
                        owner_process_epoch VARCHAR(64),
                        owner_epoch INTEGER NOT NULL DEFAULT 0,
                        lease_expires_at DATETIME,
                        state VARCHAR(16) NOT NULL DEFAULT 'active',
                        account_id VARCHAR,
                        model VARCHAR,
                        service_tier VARCHAR,
                        latest_turn_state TEXT,
                        latest_response_id TEXT,
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        closed_at DATETIME
                    )
                    """
                )
            )
    finally:
        await engine.dispose()
    await to_thread.run_sync(lambda: run_upgrade(db_url, final_revision, bootstrap_legacy=False))

    engine = create_async_engine(db_url, future=True)
    try:
        async with engine.connect() as conn:
            tables = await conn.run_sync(lambda sync_conn: set(sa_inspect(sync_conn).get_table_names()))
        assert rollup_tables <= tables

        await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(db_url), merge_revision))
        async with engine.connect() as conn:
            tables_after_downgrade = await conn.run_sync(lambda sync_conn: set(sa_inspect(sync_conn).get_table_names()))
        assert rollup_tables <= tables_after_downgrade
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_quota_warmup_claim_expiry_migration_upgrade_and_downgrade(tmp_path):
    from datetime import datetime, timezone

    from alembic import command
    from sqlalchemy import inspect as sa_inspect

    from app.db.migrate import _build_alembic_config

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'quota-warmup-claim-expiry.sqlite'}"
    parent_revision = "20260828_000000_add_accounts_chatgpt_identity_index"
    claim_revision = "20260830_000000_add_quota_warmup_claim_expiry"

    await to_thread.run_sync(lambda: run_upgrade(db_url, parent_revision, bootstrap_legacy=False))
    engine = create_async_engine(db_url, future=True)
    try:
        async with engine.begin() as conn:
            # A claim that could still be mid-probe when the migration runs
            # (claim stamp = now) and one whose claim stamp is older than any
            # probe can run (genuinely stranded by a crash).
            await conn.execute(
                text(
                    """
                    INSERT INTO quota_planner_decisions (
                        id, mode, action, account_id, scheduled_at, executed_at,
                        score, reason, forecast_snapshot_hash, state_before_json,
                        state_after_json, status, idempotency_key, created_at
                    ) VALUES (
                        'warmup-legacy-executing', 'auto', 'warmup', 'acc-legacy', NULL, NULL,
                        0.0, 'legacy_executing', NULL, NULL,
                        NULL, 'executing', 'legacy-warmup-claim', CURRENT_TIMESTAMP
                    ), (
                        'warmup-legacy-stranded', 'auto', 'warmup', 'acc-legacy', NULL,
                        '2026-01-01 00:00:00.000000',
                        0.0, 'legacy_executing', NULL, NULL,
                        NULL, 'executing', 'legacy-warmup-claim-stranded', '2026-01-01 00:00:00.000000'
                    )
                    """
                )
            )
    finally:
        await engine.dispose()

    await to_thread.run_sync(lambda: run_upgrade(db_url, claim_revision, bootstrap_legacy=False))

    engine = create_async_engine(db_url, future=True)
    try:
        async with engine.connect() as conn:
            columns = await conn.run_sync(
                lambda sync_conn: {
                    column["name"] for column in sa_inspect(sync_conn).get_columns("quota_planner_decisions")
                }
            )
            live_lease = (
                await conn.execute(
                    text("SELECT lease_expires_at FROM quota_planner_decisions WHERE id = 'warmup-legacy-executing'")
                )
            ).scalar_one()
            stranded_lease = (
                await conn.execute(
                    text("SELECT lease_expires_at FROM quota_planner_decisions WHERE id = 'warmup-legacy-stranded'")
                )
            ).scalar_one()
        assert "lease_expires_at" in columns
        # A possibly-live legacy claim keeps a conservative execution window:
        # its backfilled lease must still be in the future so a concurrent
        # pre-migration probe is not reclaimed (and duplicated) mid-flight.
        assert live_lease is not None
        assert datetime.fromisoformat(str(live_lease)) > datetime.now(timezone.utc).replace(tzinfo=None)
        # A claim stamped long before the migration is already expired and
        # recoverable on the next scheduler sweep.
        assert stranded_lease is not None
        assert datetime.fromisoformat(str(stranded_lease)) <= datetime.now(timezone.utc).replace(tzinfo=None)

        await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(db_url), parent_revision))
        async with engine.connect() as conn:
            columns_after = await conn.run_sync(
                lambda sync_conn: {
                    column["name"] for column in sa_inspect(sync_conn).get_columns("quota_planner_decisions")
                }
            )
        assert "lease_expires_at" not in columns_after
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_conversation_presence_rollup_migration_upgrade_and_downgrade(tmp_path):
    from alembic import command
    from sqlalchemy import inspect as sa_inspect

    from app.db.migrate import _build_alembic_config

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'conversation-presence-rollup.sqlite'}"
    parent_revision = "20260806_000000_add_additional_usage_alias_probe_indexes"
    rollup_revision = "20260806_010000_add_conversation_presence_rollup"
    table = "request_conversation_hourly_rollups"

    def _schema_state(sync_conn):
        inspector = sa_inspect(sync_conn)
        return {
            "has_table": inspector.has_table(table),
            "state_columns": {column["name"] for column in inspector.get_columns("account_usage_rollup_state")},
            "pk": (inspector.get_pk_constraint(table)["constrained_columns"] if inspector.has_table(table) else None),
        }

    await to_thread.run_sync(lambda: run_upgrade(db_url, parent_revision, bootstrap_legacy=False))
    engine = create_async_engine(db_url, future=True)
    try:
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
        assert not state["has_table"]
        assert "conversation_folded_through" not in state["state_columns"]

        await to_thread.run_sync(lambda: run_upgrade(db_url, rollup_revision, bootstrap_legacy=False))
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
        assert state["has_table"]
        assert "conversation_folded_through" in state["state_columns"]
        assert state["pk"] == ["bucket_epoch", "conversation_id", "account_id", "is_deleted"]

        # The migration-seeded fold-state row (older revision) must have been
        # backfilled to the epoch by the new column's server default — the
        # satellite starts its own from-zero backfill — without touching the
        # lifetime or hourly watermarks.
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT hourly_folded_through, conversation_folded_through "
                        "FROM account_usage_rollup_state WHERE id = 1"
                    )
                )
            ).one()
        assert str(row[1]).startswith("1970-01-01")

        await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(db_url), parent_revision))
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
        assert not state["has_table"]
        assert "conversation_folded_through" not in state["state_columns"]

        # Idempotent re-upgrade.
        await to_thread.run_sync(lambda: run_upgrade(db_url, rollup_revision, bootstrap_legacy=False))
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
        assert state["has_table"]
        assert "conversation_folded_through" in state["state_columns"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_file_account_pins_migration_upgrade_and_downgrade(tmp_path):
    from alembic import command
    from sqlalchemy import inspect as sa_inspect

    from app.db.migrate import _build_alembic_config

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'file-account-pins.sqlite'}"
    parent_revision = "20260806_000000_add_anonymous_telemetry"
    pin_revision = "20260813_000000_add_file_account_pins"

    def _schema_state(sync_conn):
        inspector = sa_inspect(sync_conn)
        if not inspector.has_table("file_account_pins"):
            return None
        columns = inspector.get_columns("file_account_pins")
        file_id_column = next(column for column in columns if column["name"] == "file_id")
        return {
            "columns": {column["name"] for column in columns},
            "file_id_length": file_id_column["type"].length,
            "primary_key": inspector.get_pk_constraint("file_account_pins")["constrained_columns"],
            "indexes": {index["name"] for index in inspector.get_indexes("file_account_pins")},
        }

    await to_thread.run_sync(lambda: run_upgrade(db_url, parent_revision, bootstrap_legacy=False))
    engine = create_async_engine(db_url, future=True)
    try:
        async with engine.connect() as conn:
            assert await conn.run_sync(_schema_state) is None

        await to_thread.run_sync(lambda: run_upgrade(db_url, pin_revision, bootstrap_legacy=False))
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
        assert state == {
            "columns": {"file_id", "account_id", "expires_at"},
            "file_id_length": None,
            "primary_key": ["file_id"],
            "indexes": {"ix_file_account_pins_expires_at"},
        }

        await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(db_url), parent_revision))
        async with engine.connect() as conn:
            assert await conn.run_sync(_schema_state) is None

        result = await to_thread.run_sync(lambda: run_upgrade(db_url, "head", bootstrap_legacy=False))
        assert result.current_revision == _HEAD_REVISION
        async with engine.connect() as conn:
            assert await conn.run_sync(_schema_state) is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_http_bridge_event_chunks_migration_preserves_legacy_and_guards_downgrade(tmp_path):
    from alembic import command
    from sqlalchemy import inspect as sa_inspect

    from app.db.migrate import _build_alembic_config

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'http-bridge-event-chunks.sqlite'}"
    parent_revision = "20260821_000000_add_retry_circuit_admission_generation"
    chunk_revision = "20260826_000000_add_http_bridge_event_chunks"

    def _schema_state(sync_conn):
        inspector = sa_inspect(sync_conn)
        return {
            "has_chunks": inspector.has_table("http_bridge_operation_event_chunks"),
            "operation_columns": {column["name"] for column in inspector.get_columns("http_bridge_operations")},
        }

    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    await to_thread.run_sync(lambda: run_upgrade(db_url, parent_revision, bootstrap_legacy=False))
    engine = create_async_engine(db_url, future=True)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO http_bridge_sessions (
                        id, session_key_kind, session_key_value, session_key_hash,
                        api_key_scope, owner_epoch, state, created_at, updated_at, last_seen_at
                    ) VALUES (
                        'session-legacy', 'session_header', 'legacy', 'legacy-hash',
                        '__anonymous__', 0, 'closed', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO http_bridge_operations (
                        operation_id, session_id, request_fingerprint, request_text,
                        state, response_id, recovery_dispatch_count, event_bytes,
                        event_spool_complete, created_at, updated_at
                    ) VALUES (
                        'operation-legacy', 'session-legacy', 'fingerprint-legacy', '{}',
                        'completed', 'response-legacy', 0, 8, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO http_bridge_operation_events (
                        event_id, operation_id, sequence_number, event_fingerprint, event_text, created_at
                    ) VALUES (
                        'event-legacy', 'operation-legacy', 1, 'event-fingerprint',
                        'legacy-event', CURRENT_TIMESTAMP
                    )
                    """
                )
            )

        await to_thread.run_sync(lambda: run_upgrade(db_url, chunk_revision, bootstrap_legacy=False))
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
            assert state["has_chunks"] is True
            assert "spool_format" in state["operation_columns"]
            assert (
                await conn.execute(
                    text("SELECT spool_format FROM http_bridge_operations WHERE operation_id = 'operation-legacy'")
                )
            ).scalar_one() == "rows_v1"
            assert (
                await conn.execute(
                    text("SELECT event_text FROM http_bridge_operation_events WHERE event_id = 'event-legacy'")
                )
            ).scalar_one() == "legacy-event"

        config = _build_alembic_config(db_url)
        event.listen(Engine, "connect", _enable_sqlite_foreign_keys)
        try:
            await to_thread.run_sync(lambda: command.downgrade(config, parent_revision))
        finally:
            event.remove(Engine, "connect", _enable_sqlite_foreign_keys)
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
            assert state["has_chunks"] is False
            assert "spool_format" not in state["operation_columns"]
            assert (
                await conn.execute(
                    text("SELECT event_text FROM http_bridge_operation_events WHERE event_id = 'event-legacy'")
                )
            ).scalar_one() == "legacy-event"

        await to_thread.run_sync(lambda: run_upgrade(db_url, chunk_revision, bootstrap_legacy=False))
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE http_bridge_operations SET spool_format = 'chunks_v2' "
                    "WHERE operation_id = 'operation-legacy'"
                )
            )

        with pytest.raises(RuntimeError, match="cannot downgrade while chunks_v2 operations exist"):
            await to_thread.run_sync(lambda: command.downgrade(config, parent_revision))
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE http_bridge_operations SET spool_format = 'rows_v1' WHERE operation_id = 'operation-legacy'"
                )
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO http_bridge_operation_event_chunks (
                        operation_id, first_sequence_number, event_count, codec,
                        uncompressed_bytes, payload, payload_sha256, created_at
                    ) VALUES (
                        'operation-legacy', 1, 1, 'test-codec', 1, X'00', 'chunk-hash', CURRENT_TIMESTAMP
                    )
                    """
                )
            )

        with pytest.raises(RuntimeError, match="cannot downgrade while durable transcript chunks exist"):
            await to_thread.run_sync(lambda: command.downgrade(config, parent_revision))
        async with engine.connect() as conn:
            state = await conn.run_sync(_schema_state)
            assert state["has_chunks"] is True
            assert "spool_format" in state["operation_columns"]
            assert (
                await conn.execute(text("SELECT COUNT(*) FROM http_bridge_operation_event_chunks"))
            ).scalar_one() == 1
    finally:
        await engine.dispose()
