from __future__ import annotations

from datetime import UTC, datetime

import pytest
from alembic import command
from anyio import to_thread
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.migrate import _build_alembic_config, check_schema_drift, run_upgrade
from tests.integration.desktop_reset_migration_support import migration_url as migration_url

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
RESET_PARENT = "20260910_040000_merge_reset_dashboard_user_heads"
INVITE_PARENT = "20260909_040000_add_dashboard_user_invites"
TABLES = ("dashboard_roles", "dashboard_role_grants", "dashboard_users", "dashboard_identities", "audit_logs")


@pytest.mark.parametrize("starting_revision", [RESET_PARENT, INVITE_PARENT])
async def test_invite_composition_preserves_lifecycle_and_reset_rows(migration_url, starting_revision):
    url = migration_url
    await to_thread.run_sync(lambda: run_upgrade(url, starting_revision, bootstrap_legacy=False))
    has_reset = starting_revision == RESET_PARENT
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO dashboard_roles (id,slug,name,kind) VALUES ('custom','custom','Custom','custom')")
            )
            await connection.execute(text("INSERT INTO dashboard_role_grants VALUES ('custom','accounts:read','own')"))
            await connection.execute(text("UPDATE dashboard_settings SET guest_session_generation=17 WHERE id=1"))
            for index in range(3):
                await connection.execute(
                    text(
                        "INSERT INTO dashboard_users (id,username,role_id,password_hash,session_generation) "
                        "VALUES (:id,:id,'custom',:password,:generation)"
                    ),
                    {"id": f"user-{index}", "password": f"retained-{index}", "generation": index + 11},
                )
                if not has_reset:
                    await connection.execute(
                        text(
                            "INSERT INTO dashboard_user_invites "
                            "(id,user_id,token_hash,expires_at,consumed_at,revoked_at,created_by_user_id,"
                            "sso_only,username_locked,created_at) "
                            "VALUES (:id,:user_id,:token,:expiry,:consumed,:revoked,'deleted-inviter',"
                            ":sso_only,:locked,:created)"
                        ),
                        {
                            "id": f"invite-{index}",
                            "user_id": f"user-{index}",
                            "token": f"hash-{index}".encode(),
                            "expiry": datetime(2026, 9, 20, tzinfo=UTC),
                            "consumed": datetime(2026, 9, 11, tzinfo=UTC) if index == 1 else None,
                            "revoked": datetime(2026, 9, 12, tzinfo=UTC) if index == 2 else None,
                            "sso_only": index == 1,
                            "locked": index == 2,
                            "created": datetime(2026, 9, 10, tzinfo=UTC),
                        },
                    )
            await connection.execute(
                text(
                    "INSERT INTO dashboard_identities (id,user_id,provider,provider_key,subject) "
                    "VALUES ('identity','user-0','oidc','provider','subject')"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO audit_logs (timestamp,action,actor_user_id,severity) "
                    "VALUES ('2026-09-10 12:34:56.000000','user.invited','deleted-inviter','warning')"
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
            settings_before = (await connection.execute(text("SELECT * FROM dashboard_settings"))).mappings().one()
            existing_tables = TABLES + ("desktop_reset_credit_redemptions" if has_reset else "dashboard_user_invites",)
            original = {
                table: (await connection.execute(text(f"SELECT * FROM {table} ORDER BY 1,2"))).all()
                for table in existing_tables
            }
        await to_thread.run_sync(lambda: run_upgrade(url, "head", bootstrap_legacy=False))
        await engine.dispose()
        assert await to_thread.run_sync(lambda: check_schema_drift(url)) == ()
        async with engine.connect() as connection:
            settings_after = (await connection.execute(text("SELECT * FROM dashboard_settings"))).mappings().one()
            for key, value in settings_before.items():
                assert settings_after[key] == value
            assert bool(settings_after["desktop_reset_pool_enabled"]) == has_reset
            snapshot = {
                table: (await connection.execute(text(f"SELECT * FROM {table} ORDER BY 1,2"))).all()
                for table in TABLES + ("desktop_reset_credit_redemptions", "dashboard_user_invites")
            }
            for table, rows in original.items():
                assert snapshot[table] == rows
            assert len(snapshot["dashboard_user_invites"]) == (0 if has_reset else 3)
            assert len(snapshot["desktop_reset_credit_redemptions"]) == (1 if has_reset else 0)
        await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(url), starting_revision))
        async with engine.connect() as connection:
            assert set((await connection.execute(text("SELECT version_num FROM alembic_version"))).scalars()) == {
                RESET_PARENT,
                INVITE_PARENT,
            }
            assert (
                await connection.execute(text("SELECT * FROM dashboard_settings"))
            ).mappings().one() == settings_after
            for table, rows in snapshot.items():
                assert (await connection.execute(text(f"SELECT * FROM {table} ORDER BY 1,2"))).all() == rows
        await to_thread.run_sync(lambda: run_upgrade(url, "head", bootstrap_legacy=False))
        assert await to_thread.run_sync(lambda: check_schema_drift(url)) == ()
        async with engine.connect() as connection:
            assert (
                await connection.execute(text("SELECT * FROM dashboard_settings"))
            ).mappings().one() == settings_after
            for table, rows in snapshot.items():
                assert (await connection.execute(text(f"SELECT * FROM {table} ORDER BY 1,2"))).all() == rows
    finally:
        await engine.dispose()
