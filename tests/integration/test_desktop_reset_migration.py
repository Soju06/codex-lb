from __future__ import annotations

import pytest
from alembic import command
from anyio import to_thread
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.migrate import _build_alembic_config, check_schema_drift, run_upgrade
from tests.integration.desktop_reset_migration_support import migration_url as migration_url

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
PARENT = "20260909_080000_dashboard_stream_bridge_budgets"
REVISION = "20260909_210000_desktop_reset_pool"


async def test_reset_pool_migration_defaults_existing_settings_and_round_trips(migration_url):
    url = migration_url
    await to_thread.run_sync(lambda: run_upgrade(url, PARENT, bootstrap_legacy=False))
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT COUNT(*) FROM dashboard_settings WHERE id=1")) == 1
        await to_thread.run_sync(lambda: run_upgrade(url, REVISION, bootstrap_legacy=False))
        async with engine.connect() as connection:
            assert (
                await connection.scalar(text("SELECT desktop_reset_pool_enabled FROM dashboard_settings WHERE id=1"))
                == 0
            )
        await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(url), PARENT))
        await to_thread.run_sync(lambda: run_upgrade(url, "head", bootstrap_legacy=False))
        assert await to_thread.run_sync(lambda: check_schema_drift(url)) == ()
    finally:
        await engine.dispose()


async def test_reset_pool_downgrade_retains_redemption_bindings(migration_url):
    url = migration_url
    await to_thread.run_sync(lambda: run_upgrade(url, REVISION, bootstrap_legacy=False))
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO desktop_reset_credit_redemptions "
                    "VALUES ('caller','attempt','deleted-owner','upstream-owner','credit',CURRENT_TIMESTAMP)"
                )
            )
        with pytest.raises(RuntimeError, match="redemption ledger"):
            await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(url), PARENT))
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT COUNT(*) FROM desktop_reset_credit_redemptions")) == 1
    finally:
        await engine.dispose()


@pytest.mark.parametrize("starting_revision", [REVISION, "20260910_000000_request_logs_missing_cost_index"])
async def test_populated_branch_upgrade_and_merge_downgrade_preserve_settings_and_bindings(
    migration_url, starting_revision
):
    url = migration_url
    await to_thread.run_sync(lambda: run_upgrade(url, starting_revision, bootstrap_legacy=False))
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("UPDATE dashboard_settings SET sticky_threads_enabled = false WHERE id=1"))
            if starting_revision == REVISION:
                await connection.execute(
                    text(
                        "INSERT INTO desktop_reset_credit_redemptions "
                        "VALUES ('caller','attempt','deleted-owner','upstream-owner','credit',CURRENT_TIMESTAMP)"
                    )
                )
        await to_thread.run_sync(
            lambda: run_upgrade(url, "20260910_010000_merge_desktop_reset_pool_heads", bootstrap_legacy=False)
        )
        async with engine.connect() as connection:
            assert not await connection.scalar(text("SELECT sticky_threads_enabled FROM dashboard_settings WHERE id=1"))
            assert not await connection.scalar(
                text("SELECT desktop_reset_pool_enabled FROM dashboard_settings WHERE id=1")
            )
            before = (await connection.execute(text("SELECT * FROM desktop_reset_credit_redemptions"))).all()
            assert len(before) == (1 if starting_revision == REVISION else 0)
            if before:
                assert tuple(before[0][:5]) == ("caller", "attempt", "deleted-owner", "upstream-owner", "credit")
        await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(url), starting_revision))
        async with engine.connect() as connection:
            assert (await connection.execute(text("SELECT * FROM desktop_reset_credit_redemptions"))).all() == before
            revisions = set((await connection.execute(text("SELECT version_num FROM alembic_version"))).scalars())
            assert revisions == {REVISION, "20260910_000000_request_logs_missing_cost_index"}
        await to_thread.run_sync(lambda: run_upgrade(url, "head", bootstrap_legacy=False))
        assert await to_thread.run_sync(lambda: check_schema_drift(url)) == ()
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "starting_revision",
    ["20260910_010000_merge_desktop_reset_pool_heads", "20260910_010000_dashboard_spool_retention"],
)
async def test_spool_retention_composition_preserves_values_and_binding_on_merge_downgrade(
    migration_url, starting_revision
):
    url = migration_url
    await to_thread.run_sync(lambda: run_upgrade(url, starting_revision, bootstrap_legacy=False))
    engine = create_async_engine(url)
    has_reset = starting_revision == "20260910_010000_merge_desktop_reset_pool_heads"
    try:
        async with engine.begin() as connection:
            if has_reset:
                await connection.execute(
                    text("UPDATE dashboard_settings SET desktop_reset_pool_enabled=true WHERE id=1")
                )
                await connection.execute(
                    text(
                        "INSERT INTO desktop_reset_credit_redemptions VALUES "
                        "('caller','attempt','owner','upstream-owner','credit',CURRENT_TIMESTAMP)"
                    )
                )
            else:
                await connection.execute(
                    text(
                        "UPDATE dashboard_settings SET "
                        "http_responses_session_bridge_operation_spool_retention_seconds=43200 WHERE id=1"
                    )
                )
        await to_thread.run_sync(
            lambda: run_upgrade(url, "20260910_020000_merge_reset_spool_heads", bootstrap_legacy=False)
        )
        async with engine.connect() as connection:
            before = (await connection.execute(text("SELECT * FROM desktop_reset_credit_redemptions"))).all()
            assert len(before) == (1 if has_reset else 0)
            if has_reset:
                assert tuple(before[0][:5]) == ("caller", "attempt", "owner", "upstream-owner", "credit")
        await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(url), starting_revision))
        async with engine.connect() as connection:
            assert (await connection.execute(text("SELECT * FROM desktop_reset_credit_redemptions"))).all() == before
            assert (
                bool(
                    await connection.scalar(
                        text("SELECT desktop_reset_pool_enabled FROM dashboard_settings WHERE id=1")
                    )
                )
                == has_reset
            )
            assert await connection.scalar(
                text(
                    "SELECT http_responses_session_bridge_operation_spool_retention_seconds "
                    "FROM dashboard_settings WHERE id=1"
                )
            ) == (None if has_reset else 43200)
            revisions = set((await connection.execute(text("SELECT version_num FROM alembic_version"))).scalars())
            assert revisions == {
                "20260910_010000_merge_desktop_reset_pool_heads",
                "20260910_010000_dashboard_spool_retention",
            }
        await to_thread.run_sync(lambda: run_upgrade(url, "head", bootstrap_legacy=False))
        assert await to_thread.run_sync(lambda: check_schema_drift(url)) == ()
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "starting_revision",
    ["20260910_020000_merge_reset_spool_heads", "20260908_000000_add_guest_session_generation"],
)
async def test_guest_generation_composition_preserves_authentication_and_reset_state(migration_url, starting_revision):
    url = migration_url
    await to_thread.run_sync(lambda: run_upgrade(url, starting_revision, bootstrap_legacy=False))
    has_reset = starting_revision == "20260910_020000_merge_reset_spool_heads"
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE dashboard_settings SET password_hash='retained-admin', "
                    "guest_password_hash='retained-guest', guest_access_enabled=true, "
                    "http_responses_session_bridge_operation_spool_retention_seconds=43200 WHERE id=1"
                )
            )
            if has_reset:
                await connection.execute(
                    text("UPDATE dashboard_settings SET desktop_reset_pool_enabled=true WHERE id=1")
                )
                await connection.execute(
                    text(
                        "INSERT INTO desktop_reset_credit_redemptions VALUES "
                        "('caller','attempt','owner','upstream-owner','credit',CURRENT_TIMESTAMP)"
                    )
                )
            else:
                await connection.execute(text("UPDATE dashboard_settings SET guest_session_generation=7 WHERE id=1"))
        await to_thread.run_sync(
            lambda: run_upgrade(url, "20260910_030000_merge_reset_guest_heads", bootstrap_legacy=False)
        )
        async with engine.connect() as connection:
            before = (await connection.execute(text("SELECT * FROM desktop_reset_credit_redemptions"))).all()
            assert len(before) == (1 if has_reset else 0)
            if has_reset:
                assert tuple(before[0][:5]) == ("caller", "attempt", "owner", "upstream-owner", "credit")
            settings_before = (
                await connection.execute(
                    text(
                        "SELECT password_hash, guest_password_hash, guest_access_enabled, guest_session_generation, "
                        "desktop_reset_pool_enabled, http_responses_session_bridge_operation_spool_retention_seconds "
                        "FROM dashboard_settings WHERE id=1"
                    )
                )
            ).one()
            assert tuple(settings_before) == (
                "retained-admin",
                "retained-guest",
                True,
                0 if has_reset else 7,
                has_reset,
                43200,
            )
        await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(url), starting_revision))
        async with engine.connect() as connection:
            assert (await connection.execute(text("SELECT * FROM desktop_reset_credit_redemptions"))).all() == before
            settings_after = (
                await connection.execute(
                    text(
                        "SELECT password_hash, guest_password_hash, guest_access_enabled, guest_session_generation, "
                        "desktop_reset_pool_enabled, http_responses_session_bridge_operation_spool_retention_seconds "
                        "FROM dashboard_settings WHERE id=1"
                    )
                )
            ).one()
            assert settings_after == settings_before
            revisions = set((await connection.execute(text("SELECT version_num FROM alembic_version"))).scalars())
            assert revisions == {
                "20260910_020000_merge_reset_spool_heads",
                "20260908_000000_add_guest_session_generation",
            }
        await to_thread.run_sync(lambda: run_upgrade(url, "head", bootstrap_legacy=False))
        assert await to_thread.run_sync(lambda: check_schema_drift(url)) == ()
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "starting_revision",
    ["20260910_030000_merge_reset_guest_heads", "20260909_030000_add_audit_actor_columns"],
)
async def test_dashboard_user_composition_preserves_roles_sessions_and_reset_bindings(migration_url, starting_revision):
    url = migration_url
    await to_thread.run_sync(lambda: run_upgrade(url, starting_revision, bootstrap_legacy=False))
    has_reset = starting_revision == "20260910_030000_merge_reset_guest_heads"
    engine = create_async_engine(url)
    tables = ("dashboard_roles", "dashboard_role_grants", "dashboard_users", "dashboard_identities", "audit_logs")
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO audit_logs (timestamp,action,actor_ip,details,request_id) "
                    "VALUES ('2026-09-10 12:34:56.000000','settings.updated','127.0.0.1','retained','request')"
                )
            )
            await connection.execute(
                text(
                    "UPDATE dashboard_settings SET password_hash='legacy-password', "
                    "totp_secret_encrypted=:secret, totp_last_verified_step=42, "
                    "guest_password_hash='guest-password', guest_access_enabled=true, "
                    "guest_session_generation=9, "
                    "http_responses_session_bridge_operation_spool_retention_seconds=43200 WHERE id=1"
                ),
                {"secret": b"retained-totp"},
            )
            settings_before = (await connection.execute(text("SELECT * FROM dashboard_settings"))).mappings().one()
            if has_reset:
                original_audit = (await connection.execute(text("SELECT * FROM audit_logs"))).mappings().one()
                await connection.execute(
                    text("UPDATE dashboard_settings SET desktop_reset_pool_enabled=true WHERE id=1")
                )
                await connection.execute(
                    text(
                        "INSERT INTO desktop_reset_credit_redemptions VALUES "
                        "('caller','attempt','owner','upstream-owner','credit',CURRENT_TIMESTAMP)"
                    )
                )
                original_bindings = (
                    await connection.execute(text("SELECT * FROM desktop_reset_credit_redemptions"))
                ).all()
            else:
                await connection.execute(
                    text(
                        "UPDATE audit_logs SET actor_user_id='existing-user',actor_username='existing-user', "
                        "actor_role_slug='custom',auth_method='password',target_type='settings', "
                        "target_id='dashboard',severity='warning'"
                    )
                )
                await connection.execute(
                    text("INSERT INTO dashboard_roles (id,slug,name,kind) VALUES ('custom','custom','Custom','custom')")
                )
                await connection.execute(
                    text("INSERT INTO dashboard_role_grants VALUES ('custom','dashboard:read','all')")
                )
                await connection.execute(
                    text(
                        "INSERT INTO dashboard_users (id,username,role_id,password_hash,session_generation) "
                        "VALUES ('existing-user','existing-user','custom','user-password',13)"
                    )
                )
                await connection.execute(
                    text(
                        "INSERT INTO dashboard_identities (id,user_id,provider,provider_key,subject) "
                        "VALUES ('identity','existing-user','oidc','provider','subject')"
                    )
                )
                original_rows = {
                    table: (await connection.execute(text(f"SELECT * FROM {table} ORDER BY 1,2"))).all()
                    for table in tables
                }
        await to_thread.run_sync(
            lambda: run_upgrade(url, "20260910_040000_merge_reset_dashboard_user_heads", bootstrap_legacy=False)
        )
        # Alembic uses another connection; discard prepared SELECT * plans after its DDL.
        await engine.dispose()
        async with engine.connect() as connection:
            settings_after = (await connection.execute(text("SELECT * FROM dashboard_settings"))).mappings().one()
            for key, value in settings_before.items():
                if key != "desktop_reset_pool_enabled":
                    assert settings_after[key] == value
            assert bool(settings_after["desktop_reset_pool_enabled"]) == has_reset
            bindings = (await connection.execute(text("SELECT * FROM desktop_reset_credit_redemptions"))).all()
            assert len(bindings) == (1 if has_reset else 0)
            if has_reset:
                assert bindings == original_bindings
                assert tuple(bindings[0][:5]) == ("caller", "attempt", "owner", "upstream-owner", "credit")
                admin = (
                    await connection.execute(
                        text(
                            "SELECT username,password_hash,totp_secret_encrypted,totp_last_verified_step, "
                            "session_generation FROM dashboard_users"
                        )
                    )
                ).one()
                assert tuple(admin) == ("admin", "legacy-password", b"retained-totp", 42, 0)
                assert set((await connection.execute(text("SELECT slug FROM dashboard_roles"))).scalars()) == {
                    "admin",
                    "operator",
                    "viewer",
                    "guest",
                    "member",
                }
                migrated_audit = (await connection.execute(text("SELECT * FROM audit_logs"))).mappings().one()
                for key, value in original_audit.items():
                    assert migrated_audit[key] == value
                audit = (
                    await connection.execute(
                        text(
                            "SELECT action,actor_ip,details,request_id,actor_user_id,actor_username,actor_role_slug, "
                            "auth_method,target_type,target_id,severity FROM audit_logs"
                        )
                    )
                ).one()
                assert tuple(audit) == (
                    "settings.updated",
                    "127.0.0.1",
                    "retained",
                    "request",
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    "info",
                )
            snapshot = {
                table: (await connection.execute(text(f"SELECT * FROM {table} ORDER BY 1,2"))).all() for table in tables
            }
            if not has_reset:
                assert snapshot == original_rows
        await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(url), starting_revision))
        async with engine.connect() as connection:
            assert set((await connection.execute(text("SELECT version_num FROM alembic_version"))).scalars()) == {
                "20260910_030000_merge_reset_guest_heads",
                "20260909_030000_add_audit_actor_columns",
            }
            assert (await connection.execute(text("SELECT * FROM desktop_reset_credit_redemptions"))).all() == bindings
            assert (
                await connection.execute(text("SELECT * FROM dashboard_settings"))
            ).mappings().one() == settings_after
            assert {
                table: (await connection.execute(text(f"SELECT * FROM {table} ORDER BY 1,2"))).all() for table in tables
            } == snapshot
        await to_thread.run_sync(lambda: run_upgrade(url, "head", bootstrap_legacy=False))
        assert await to_thread.run_sync(lambda: check_schema_drift(url)) == ()
        async with engine.connect() as connection:
            assert (await connection.execute(text("SELECT * FROM desktop_reset_credit_redemptions"))).all() == bindings
            assert (
                await connection.execute(text("SELECT * FROM dashboard_settings"))
            ).mappings().one() == settings_after
            assert {
                table: (await connection.execute(text(f"SELECT * FROM {table} ORDER BY 1,2"))).all() for table in tables
            } == snapshot
    finally:
        await engine.dispose()
