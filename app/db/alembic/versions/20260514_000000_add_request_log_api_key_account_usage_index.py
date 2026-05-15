"""add request_logs account_deleted

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


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("request_logs"):
        return

    columns = {column["name"] for column in inspector.get_columns("request_logs")}
    if "account_deleted" in columns:
        with op.batch_alter_table("request_logs") as batch_op:
            batch_op.drop_column("account_deleted")
