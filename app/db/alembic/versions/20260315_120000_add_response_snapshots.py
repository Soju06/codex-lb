"""add durable response snapshots

Revision ID: 20260315_120000_add_response_snapshots
Revises: 20260312_120000_add_dashboard_upstream_stream_transport
Create Date: 2026-03-15 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260315_120000_add_response_snapshots"
down_revision = "20260312_120000_add_dashboard_upstream_stream_transport"
branch_labels = None
depends_on = None


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def _indexes(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(index["name"]) for index in inspector.get_indexes(table_name) if index.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "response_snapshots"):
        op.create_table(
            "response_snapshots",
            sa.Column("response_id", sa.String(), nullable=False),
            sa.Column("parent_response_id", sa.String(), nullable=True),
            sa.Column("account_id", sa.String(), nullable=True),
            sa.Column("model", sa.String(), nullable=False),
            sa.Column("input_items_json", sa.Text(), nullable=False),
            sa.Column("response_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("response_id"),
        )
    existing_indexes = _indexes(bind, "response_snapshots")
    if "idx_response_snapshots_parent_created_at" not in existing_indexes:
        op.create_index(
            "idx_response_snapshots_parent_created_at",
            "response_snapshots",
            ["parent_response_id", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    return
