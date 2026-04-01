"""add api key enforced service tier

Revision ID: 20260401_000000_add_api_key_enforced_service_tier
Revises: 20260330_000000_add_cache_locality_settings
Create Date: 2026-04-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260401_000000_add_api_key_enforced_service_tier"
down_revision = "20260330_000000_add_cache_locality_settings"
branch_labels = None
depends_on = None


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "api_keys"):
        return

    existing_columns = _columns(bind, "api_keys")
    with op.batch_alter_table("api_keys") as batch_op:
        if "enforced_service_tier" not in existing_columns:
            batch_op.add_column(sa.Column("enforced_service_tier", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "api_keys"):
        return

    existing_columns = _columns(bind, "api_keys")
    with op.batch_alter_table("api_keys") as batch_op:
        if "enforced_service_tier" in existing_columns:
            batch_op.drop_column("enforced_service_tier")
