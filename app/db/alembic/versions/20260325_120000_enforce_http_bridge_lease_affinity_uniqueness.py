"""enforce http bridge lease affinity uniqueness

Revision ID: 20260325_120000_enforce_http_bridge_lease_affinity_uniqueness
Revises: 20260322_000000_merge_http_bridge_lease_head
Create Date: 2026-03-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260325_120000_enforce_http_bridge_lease_affinity_uniqueness"
down_revision = "20260322_000000_merge_http_bridge_lease_head"
branch_labels = None
depends_on = None


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def _index_exists(connection: Connection, index_name: str, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return False
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "http_bridge_leases"):
        return
    op.execute(
        sa.text(
            """
            DELETE FROM http_bridge_leases
            WHERE session_id IN (
                SELECT session_id
                FROM (
                    SELECT
                        session_id,
                        ROW_NUMBER() OVER (
                            PARTITION BY affinity_kind, affinity_key, api_key_scope
                            ORDER BY lease_expires_at DESC, updated_at DESC, created_at DESC, session_id DESC
                        ) AS row_num
                    FROM http_bridge_leases
                ) ranked_leases
                WHERE ranked_leases.row_num > 1
            )
            """
        )
    )
    if not _index_exists(bind, "ux_http_bridge_leases_affinity_scope", "http_bridge_leases"):
        op.create_index(
            "ux_http_bridge_leases_affinity_scope",
            "http_bridge_leases",
            ["affinity_kind", "affinity_key", "api_key_scope"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "http_bridge_leases") and _index_exists(
        bind,
        "ux_http_bridge_leases_affinity_scope",
        "http_bridge_leases",
    ):
        op.drop_index("ux_http_bridge_leases_affinity_scope", table_name="http_bridge_leases")
