"""add performance indexes for sqlite startup query paths

Revision ID: 20260308_000000_add_sqlite_performance_indexes
Revises: 20260307_000000_add_api_key_enforcement_fields
Create Date: 2026-03-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

from app.db.dialect_sql import is_mysql

# revision identifiers, used by Alembic.
revision = "20260308_000000_add_sqlite_performance_indexes"
down_revision = "20260307_000000_add_api_key_enforcement_fields"
branch_labels = None
depends_on = None


def _index_exists(connection: Connection, index_name: str, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return False
    return any(idx["name"] == index_name for idx in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    if is_mysql(bind):
        # MySQL has no CREATE INDEX IF NOT EXISTS, and functional key parts need
        # their expression in parentheses. ``window`` is a reserved word and must
        # be backquoted here: with MySQL's default sql_mode, "window" would be a
        # string literal and the functional part would index a constant.
        if not _index_exists(bind, "idx_usage_window_account_latest", "usage_history"):
            op.execute(
                sa.text(
                    "CREATE INDEX idx_usage_window_account_latest ON usage_history "
                    "((coalesce(`window`, 'primary')), account_id, recorded_at DESC, id DESC)"
                )
            )
        if not _index_exists(bind, "idx_logs_requested_at_id", "request_logs"):
            op.execute(sa.text("CREATE INDEX idx_logs_requested_at_id ON request_logs (requested_at DESC, id DESC)"))
        return

    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS idx_usage_window_account_latest
            ON usage_history (coalesce("window", 'primary'), account_id, recorded_at DESC, id DESC)
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS idx_logs_requested_at_id
            ON request_logs (requested_at DESC, id DESC)
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if is_mysql(bind):
        if _index_exists(bind, "idx_logs_requested_at_id", "request_logs"):
            op.execute(sa.text("DROP INDEX idx_logs_requested_at_id ON request_logs"))
        if _index_exists(bind, "idx_usage_window_account_latest", "usage_history"):
            op.execute(sa.text("DROP INDEX idx_usage_window_account_latest ON usage_history"))
        return

    op.execute(sa.text("DROP INDEX IF EXISTS idx_logs_requested_at_id"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_usage_window_account_latest"))
