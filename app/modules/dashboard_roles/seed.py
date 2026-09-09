"""Idempotent seeding of the preset ``dashboard_roles`` rows.

The Alembic revision (with its own revision-pinned table definition) and the
test schema reset (which builds the schema with ``create_all`` instead of
Alembic) both call this so the five preset rows exist wherever the table does. Rows are inserted with
"do nothing on conflict" semantics and are never updated: preset grants live
in code (``PRESET_ROLE_GRANTS``), so there is nothing version-dependent to
reconcile and a replica running older code can never strip a newer grant.
"""

from __future__ import annotations

from sqlalchemy import Table, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.auth.dashboard_access import (
    ASSIGNABLE_PRESET_ROLES,
    PRESET_ROLE_IDS,
    PRESET_ROLE_NAMES,
    PresetRoleSlug,
    RoleKind,
)
from app.db.models import Base, DashboardRoleRecord

_ROLES_TABLE: Table = Base.metadata.tables[DashboardRoleRecord.__tablename__]

_PRESET_DESCRIPTIONS: dict[PresetRoleSlug, str] = {
    PresetRoleSlug.ADMIN: "Everything: users, security settings, credentials export, conversations, audit.",
    PresetRoleSlug.OPERATOR: "Day-to-day operations: accounts, all API keys, model sources, automations.",
    PresetRoleSlug.MEMBER: "Own API keys and own usage only.",
    PresetRoleSlug.VIEWER: "Read-only dashboard, reports and account status.",
    PresetRoleSlug.GUEST: "Anonymous shared read-only session; cannot be assigned to a user.",
}


def preset_role_rows() -> list[dict[str, object]]:
    return [
        {
            "id": PRESET_ROLE_IDS[slug],
            "slug": slug.value,
            "name": PRESET_ROLE_NAMES[slug],
            "description": _PRESET_DESCRIPTIONS[slug],
            "kind": RoleKind.PRESET.value,
            "assignable_to_users": slug in ASSIGNABLE_PRESET_ROLES,
            "permissions_version": 1,
        }
        for slug in PresetRoleSlug
    ]


def _insert_ignore(connection: Connection, table: Table, rows: list[dict[str, object]]) -> None:
    dialect = connection.dialect.name
    if dialect == "postgresql":
        stmt = postgresql.insert(table).values(rows).on_conflict_do_nothing(index_elements=["id"])
        connection.execute(stmt)
        return
    if dialect == "sqlite":
        stmt = sqlite.insert(table).values(rows).on_conflict_do_nothing(index_elements=["id"])
        connection.execute(stmt)
        return
    existing = {row[0] for row in connection.execute(select(table.c.id)).all()}
    missing = [row for row in rows if row["id"] not in existing]
    if missing:
        connection.execute(table.insert().values(missing))


def seed_preset_dashboard_roles(connection: Connection, table: Table = _ROLES_TABLE) -> None:
    """Insert any missing preset role row using a synchronous connection.

    The migration passes its own revision-pinned table so it stays frozen
    when the ORM model evolves; every other caller uses the current model.
    """

    _insert_ignore(connection, table, preset_role_rows())


async def ensure_preset_dashboard_roles(connection: AsyncConnection) -> None:
    await connection.run_sync(seed_preset_dashboard_roles)
