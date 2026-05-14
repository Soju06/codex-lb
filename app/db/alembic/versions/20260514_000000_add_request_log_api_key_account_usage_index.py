"""add request_logs account_deleted and api key account usage index

Revision ID: 20260514_000000_add_request_log_api_key_account_usage_index
Revises: 20260424_000000_merge_dashboard_session_ttl_and_request_log_heads
Create Date: 2026-05-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260514_000000_add_request_log_api_key_account_usage_index"
down_revision = "20260424_000000_merge_dashboard_session_ttl_and_request_log_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("request_logs"):
        return

    columns = {column["name"] for column in inspector.get_columns("request_logs")}
    if "account_deleted" not in columns:
        with op.batch_alter_table("request_logs") as batch_op:
            batch_op.add_column(
                sa.Column("account_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
            )

    existing_indexes = {index["name"] for index in inspector.get_indexes("request_logs")}
    if "idx_logs_api_key_time_account" not in existing_indexes:
        op.create_index(
            "idx_logs_api_key_time_account",
            "request_logs",
            ["api_key_id", "requested_at", "account_deleted", "account_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("request_logs"):
        return

    existing_indexes = {index["name"] for index in inspector.get_indexes("request_logs")}
    if "idx_logs_api_key_time_account" in existing_indexes:
        op.drop_index("idx_logs_api_key_time_account", table_name="request_logs")

    columns = {column["name"] for column in inspector.get_columns("request_logs")}
    if "account_deleted" in columns:
        with op.batch_alter_table("request_logs") as batch_op:
            batch_op.drop_column("account_deleted")
