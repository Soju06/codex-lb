"""add http_bridge_leases table

Revision ID: 20260321_120000_add_http_bridge_leases
Revises: 20260320_000000_add_request_log_requested_actual_tiers
Create Date: 2026-03-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260321_120000_add_http_bridge_leases"
down_revision = "20260320_000000_add_request_log_requested_actual_tiers"
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
        op.create_table(
            "http_bridge_leases",
            sa.Column("session_id", sa.String(), primary_key=True),
            sa.Column("affinity_kind", sa.String(), nullable=False),
            sa.Column("affinity_key", sa.String(), nullable=False),
            sa.Column("api_key_scope", sa.String(), nullable=False, server_default=sa.text("''")),
            sa.Column("owner_instance_id", sa.String(), nullable=False),
            sa.Column("lease_expires_at", sa.DateTime(), nullable=False),
            sa.Column("account_id", sa.String(), nullable=True),
            sa.Column("request_model", sa.String(), nullable=True),
            sa.Column("codex_session", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("idle_ttl_seconds", sa.Float(), nullable=False),
            sa.Column("upstream_turn_state", sa.String(), nullable=True),
            sa.Column("downstream_turn_state", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
    if not _index_exists(bind, "ix_http_bridge_leases_owner_expires", "http_bridge_leases"):
        op.create_index(
            "ix_http_bridge_leases_owner_expires",
            "http_bridge_leases",
            ["owner_instance_id", "lease_expires_at"],
        )
    if not _index_exists(bind, "ix_http_bridge_leases_expires", "http_bridge_leases"):
        op.create_index(
            "ix_http_bridge_leases_expires",
            "http_bridge_leases",
            ["lease_expires_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "http_bridge_leases"):
        if _index_exists(bind, "ix_http_bridge_leases_expires", "http_bridge_leases"):
            op.drop_index("ix_http_bridge_leases_expires", table_name="http_bridge_leases")
        if _index_exists(bind, "ix_http_bridge_leases_owner_expires", "http_bridge_leases"):
            op.drop_index("ix_http_bridge_leases_owner_expires", table_name="http_bridge_leases")
        op.drop_table("http_bridge_leases")
