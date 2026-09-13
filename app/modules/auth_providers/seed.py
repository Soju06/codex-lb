"""Idempotent seeding of the built-in ``dashboard_auth_providers`` rows.

The Alembic revision (with its revision-pinned table) and the test schema
reset (``create_all``) both call this so the password and trusted-header rows
exist wherever the table does. Rows are inserted with "do nothing on conflict"
semantics and never updated, so an operator's later PATCH survives restarts
and upgrades. The trusted-header row hands unknown identities the admin
preset (owner decision D10: an upgraded reverse-proxy install keeps admitting
everyone the proxy vouches for) and parks mapped users without a match on the
viewer preset.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Table, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.db.models import AuthProviderKind, Base, DashboardAuthProvider

_PROVIDERS_TABLE: Table = Base.metadata.tables[DashboardAuthProvider.__tablename__]
_PROVIDER_ID_NAMESPACE = uuid.UUID("2c8f6a4e-7d3b-4f1a-9e5c-1b0d8a7f3e21")

#: The one instance of a provider kind that has no per-instance configuration.
DEFAULT_PROVIDER_KEY = "default"


def auth_provider_id(kind: AuthProviderKind, provider_key: str = DEFAULT_PROVIDER_KEY) -> str:
    """Deterministic id of a built-in provider row (UUIDv5 of kind and key)."""

    return str(uuid.uuid5(_PROVIDER_ID_NAMESPACE, f"codex-lb:auth-provider:{kind.value}:{provider_key}"))


def default_auth_provider_rows() -> list[dict[str, object]]:
    return [
        {
            "id": auth_provider_id(AuthProviderKind.PASSWORD),
            "kind": AuthProviderKind.PASSWORD.value,
            "provider_key": DEFAULT_PROVIDER_KEY,
            "enabled": True,
            "label": "Password",
            "unknown_identity_role_id": None,
            "no_match_role_id": None,
            "link_by_email": False,
            "skip_role_sync": False,
            "idp_mfa_enforced": False,
        },
        {
            "id": auth_provider_id(AuthProviderKind.TRUSTED_HEADER),
            "kind": AuthProviderKind.TRUSTED_HEADER.value,
            "provider_key": DEFAULT_PROVIDER_KEY,
            "enabled": True,
            "label": "Reverse proxy",
            "unknown_identity_role_id": PRESET_ROLE_IDS[PresetRoleSlug.ADMIN],
            "no_match_role_id": PRESET_ROLE_IDS[PresetRoleSlug.VIEWER],
            "link_by_email": False,
            "skip_role_sync": False,
            "idp_mfa_enforced": False,
        },
    ]


def _insert_ignore(connection: Connection, table: Table, rows: list[dict[str, object]]) -> None:
    dialect = connection.dialect.name
    if dialect == "postgresql":
        connection.execute(postgresql.insert(table).values(rows).on_conflict_do_nothing(index_elements=["id"]))
        return
    if dialect == "sqlite":
        connection.execute(sqlite.insert(table).values(rows).on_conflict_do_nothing(index_elements=["id"]))
        return
    existing = {row[0] for row in connection.execute(select(table.c.id)).all()}
    missing = [row for row in rows if row["id"] not in existing]
    if missing:
        connection.execute(table.insert().values(missing))


def seed_default_auth_providers(connection: Connection, table: Table = _PROVIDERS_TABLE) -> None:
    """Insert any missing built-in provider row using a synchronous connection."""

    _insert_ignore(connection, table, default_auth_provider_rows())


async def ensure_default_auth_providers(connection: AsyncConnection) -> None:
    await connection.run_sync(seed_default_auth_providers)
