"""add cache invalidation table

Revision ID: 20260401_000000_add_cache_invalidation
Revises: 20260330_020000_add_bridge_ring_members
Create Date: 2026-04-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260401_000000_add_cache_invalidation"
down_revision = "20260330_020000_add_bridge_ring_members"
branch_labels = None
depends_on = None


def _has_table(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "cache_invalidation"):
        return

    op.create_table(
        "cache_invalidation",
        sa.Column("namespace", sa.String(50), primary_key=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="0"),
    )

    op.execute(sa.text("INSERT INTO cache_invalidation (namespace, version) VALUES ('api_key', 0), ('firewall', 0)"))


def downgrade() -> None:
    op.drop_table("cache_invalidation")
