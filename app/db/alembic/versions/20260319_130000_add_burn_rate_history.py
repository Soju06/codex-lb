"""add burn_rate_history table

Revision ID: 20260319_130000_add_burn_rate_history
Revises: 20260312_120000_add_dashboard_upstream_stream_transport
Create Date: 2026-03-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260319_130000_add_burn_rate_history"
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

    if not _table_exists(bind, "burn_rate_history"):
        op.create_table(
            "burn_rate_history",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("recorded_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("primary_projected_plus_accounts", sa.Float(), nullable=True),
            sa.Column("secondary_projected_plus_accounts", sa.Float(), nullable=True),
            sa.Column("primary_used_plus_accounts", sa.Float(), nullable=True),
            sa.Column("secondary_used_plus_accounts", sa.Float(), nullable=True),
            sa.Column("primary_window_minutes", sa.Integer(), nullable=True),
            sa.Column("secondary_window_minutes", sa.Integer(), nullable=True),
            sa.Column("primary_account_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("secondary_account_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column(
                "primary_max_plus_equivalent_accounts",
                sa.Float(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "secondary_max_plus_equivalent_accounts",
                sa.Float(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )

    existing_indexes = _indexes(bind, "burn_rate_history")
    if "idx_burn_rate_recorded_at" not in existing_indexes:
        op.create_index("idx_burn_rate_recorded_at", "burn_rate_history", ["recorded_at"])


def downgrade() -> None:
    op.drop_table("burn_rate_history")
