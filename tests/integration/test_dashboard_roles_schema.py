from __future__ import annotations

import pytest
from anyio import to_thread
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.auth.dashboard_access import PRESET_ROLE_IDS, Permission, PresetRoleSlug, RoleKind, Scope
from app.core.config.settings import get_settings
from app.db.migrate import _build_alembic_config, inspect_migration_state, run_upgrade
from app.db.models import DashboardRoleGrant, DashboardRoleRecord
from app.db.session import SessionLocal
from app.modules.dashboard_roles.repository import DashboardRolesRepository
from app.modules.dashboard_roles.seed import ensure_preset_dashboard_roles
from app.modules.dashboard_roles.service import resolve_role_grants

pytestmark = pytest.mark.integration

_HEAD_REVISION = inspect_migration_state(get_settings().database_url).head_revision
PARENT_REVISION = "20260908_000000_add_guest_session_generation"
TARGET_REVISION = "20260909_000000_add_dashboard_roles"


@pytest.mark.asyncio
async def test_preset_rows_exist_after_schema_reset(db_setup) -> None:
    async with SessionLocal() as session:
        roles = await DashboardRolesRepository(session).list_roles()
    by_slug = {role.slug: role for role in roles}
    assert set(by_slug) == {slug.value for slug in PresetRoleSlug}
    for slug in PresetRoleSlug:
        role = by_slug[slug.value]
        assert role.id == PRESET_ROLE_IDS[slug]
        assert role.kind == RoleKind.PRESET.value
        assert role.grants == []
    assert by_slug["guest"].assignable_to_users is False


@pytest.mark.asyncio
async def test_seeding_is_idempotent_and_never_updates_existing_rows(db_setup) -> None:
    from app.db.session import engine

    async with engine.begin() as connection:
        # A missing row is re-inserted; an existing row is left untouched even
        # when it differs from the code's definition (presets are insert-only).
        await connection.execute(text("DELETE FROM dashboard_roles WHERE slug = 'viewer'"))
        await connection.execute(text("UPDATE dashboard_roles SET name = 'Renamed operator' WHERE slug = 'operator'"))
        await ensure_preset_dashboard_roles(connection)
        await ensure_preset_dashboard_roles(connection)
    async with SessionLocal() as session:
        rows = (await session.execute(text("SELECT id, slug, name FROM dashboard_roles ORDER BY slug"))).all()
    assert len(rows) == len(PresetRoleSlug)
    by_slug = {row[1]: row for row in rows}
    assert by_slug["viewer"][0] == PRESET_ROLE_IDS[PresetRoleSlug.VIEWER]
    assert by_slug["operator"][2] == "Renamed operator"


@pytest.mark.asyncio
async def test_custom_role_grants_round_trip_and_presets_resolve_from_code(db_setup) -> None:
    async with SessionLocal() as session:
        custom = DashboardRoleRecord(
            slug="ops-lite", name="Ops lite", kind=RoleKind.CUSTOM.value, cloned_from_role_id=None
        )
        custom.grants = [
            DashboardRoleGrant(permission="dashboard:read", scope="all"),
            DashboardRoleGrant(permission="api_keys:read", scope="own"),
            DashboardRoleGrant(permission="not:a-permission", scope="all"),
        ]
        session.add(custom)
        await session.commit()
        custom_id = custom.id

    async with SessionLocal() as session:
        repo = DashboardRolesRepository(session)
        loaded = await repo.get_role(custom_id)
        assert loaded is not None
        assert dict(resolve_role_grants(loaded)) == {
            Permission.DASHBOARD_READ: Scope.ALL,
            Permission.API_KEYS_READ: Scope.OWN,
        }
        admin = await repo.get_preset_role(PresetRoleSlug.ADMIN)
        assert admin is not None
        assert set(resolve_role_grants(admin)) == set(Permission)
        # Deleting the custom role cascades its grants.
        await session.delete(loaded)
        await session.commit()
        remaining = (
            await session.execute(select(DashboardRoleGrant).where(DashboardRoleGrant.role_id == custom_id))
        ).all()
        assert remaining == []


@pytest.mark.asyncio
async def test_dashboard_roles_migration_upgrade_and_downgrade(tmp_path):
    """Upgrade creates both tables and seeds the five preset rows (re-run safe);
    downgrade drops them; a final walk to head proves the single-head graph."""
    from alembic import command

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'dashboard-roles.sqlite'}"

    async def _tables(engine) -> set[str]:
        async with engine.connect() as conn:
            rows = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            return {row[0] for row in rows}

    await to_thread.run_sync(lambda: run_upgrade(db_url, PARENT_REVISION, bootstrap_legacy=False))
    engine = create_async_engine(db_url, future=True)
    try:
        assert not {"dashboard_roles", "dashboard_role_grants"} & await _tables(engine)

        await to_thread.run_sync(lambda: run_upgrade(db_url, TARGET_REVISION, bootstrap_legacy=False))
        assert {"dashboard_roles", "dashboard_role_grants"} <= await _tables(engine)
        async with engine.connect() as conn:
            rows = (await conn.execute(text("SELECT id, slug, kind, assignable_to_users FROM dashboard_roles"))).all()
            grants = (await conn.execute(text("SELECT COUNT(*) FROM dashboard_role_grants"))).scalar_one()
        assert {row[1] for row in rows} == {slug.value for slug in PresetRoleSlug}
        assert {row[0] for row in rows} == set(PRESET_ROLE_IDS.values())
        assert all(row[2] == RoleKind.PRESET.value for row in rows)
        assert grants == 0

        config = _build_alembic_config(db_url)
        await to_thread.run_sync(lambda: command.downgrade(config, PARENT_REVISION))
        assert not {"dashboard_roles", "dashboard_role_grants"} & await _tables(engine)

        result = await to_thread.run_sync(lambda: run_upgrade(db_url, "head", bootstrap_legacy=False))
        assert result.current_revision == _HEAD_REVISION
        async with engine.connect() as conn:
            count = (await conn.execute(text("SELECT COUNT(*) FROM dashboard_roles"))).scalar_one()
        assert count == len(PresetRoleSlug)
    finally:
        await engine.dispose()
