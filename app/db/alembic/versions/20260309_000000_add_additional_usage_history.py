"""add additional_usage_history table

Revision ID: 20260309_000000_add_additional_usage_history
Revises: 20260309_000000_request_logs_nullable_account_id
Create Date: 2026-03-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260309_000000_add_additional_usage_history"
down_revision = "20260309_000000_request_logs_nullable_account_id"
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

    if not _table_exists(bind, "additional_usage_history"):
        op.create_table(
            "additional_usage_history",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("account_id", sa.String(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
            sa.Column("limit_name", sa.String(), nullable=False),
            sa.Column("metered_feature", sa.String(), nullable=False),
            sa.Column("window", sa.String(), nullable=False),
            sa.Column("used_percent", sa.Float(), nullable=False),
            sa.Column("reset_at", sa.Integer(), nullable=True),
            sa.Column("window_minutes", sa.Integer(), nullable=True),
            sa.Column("recorded_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )

    existing_indexes = _indexes(bind, "additional_usage_history")
    if "ix_additional_usage_history_account_id" not in existing_indexes:
        op.create_index("ix_additional_usage_history_account_id", "additional_usage_history", ["account_id"])
    if "ix_additional_usage_history_recorded_at" not in existing_indexes:
        op.create_index("ix_additional_usage_history_recorded_at", "additional_usage_history", ["recorded_at"])
    if "ix_additional_usage_history_composite" not in existing_indexes:
        op.create_index(
            "ix_additional_usage_history_composite",
            "additional_usage_history",
            ["account_id", "limit_name", "window", "recorded_at"],
        )
    # Index for latest_by_account queries that filter on (limit_name, window)
    if "ix_additional_usage_limit_window" not in existing_indexes:
        op.create_index(
            "ix_additional_usage_limit_window",
            "additional_usage_history",
            ["limit_name", "window", "account_id", "recorded_at"],
        )


def downgrade() -> None:
    op.drop_table("additional_usage_history")
