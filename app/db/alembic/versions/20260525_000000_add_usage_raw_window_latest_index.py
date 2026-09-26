"""add raw window latest usage index

Revision ID: 20260525_000000_add_usage_raw_window_latest_index
Revises: 20260513_000000_add_accounts_alias
Create Date: 2026-05-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.migration_indexes import create_mysql_index, index_exists, is_mysql

revision = "20260525_000000_add_usage_raw_window_latest_index"
down_revision = "20260513_000000_add_accounts_alias"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if is_mysql(bind):
        create_mysql_index(
            bind,
            index_name="idx_usage_window_raw_account_latest",
            table_name="usage_history",
            columns_sql="`window`, `account_id`, `recorded_at` DESC, `id` DESC",
        )
        return

    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS idx_usage_window_raw_account_latest
            ON usage_history ("window", account_id, recorded_at DESC, id DESC)
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if is_mysql(bind):
        if index_exists(bind, "idx_usage_window_raw_account_latest", "usage_history"):
            op.execute(sa.text("DROP INDEX idx_usage_window_raw_account_latest ON usage_history"))
        return

    op.execute(sa.text("DROP INDEX IF EXISTS idx_usage_window_raw_account_latest"))
