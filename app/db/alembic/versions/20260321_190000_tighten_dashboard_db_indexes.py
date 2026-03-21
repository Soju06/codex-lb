"""tighten dashboard db indexes

Revision ID: 20260321_190000_tighten_dashboard_db_indexes
Revises: 20260319_183000_normalize_sqlite_account_status_casing
Create Date: 2026-03-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260321_190000_tighten_dashboard_db_indexes"
down_revision = "20260319_183000_normalize_sqlite_account_status_casing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS idx_usage_window_account_time
            ON usage_history (coalesce("window", 'primary'), account_id, recorded_at)
            """
        )
    )
    op.create_index(
        "idx_api_keys_name",
        "api_keys",
        ["name"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "idx_logs_requested_at_model_tier",
        "request_logs",
        [sa.text("requested_at DESC"), "model", "service_tier"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "idx_logs_model_effort_time",
        "request_logs",
        ["model", "reasoning_effort", sa.text("requested_at DESC"), sa.text("id DESC")],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "idx_logs_status_error_time",
        "request_logs",
        ["status", "error_code", sa.text("requested_at DESC"), sa.text("id DESC")],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("idx_logs_status_error_time", table_name="request_logs", if_exists=True)
    op.drop_index("idx_logs_model_effort_time", table_name="request_logs", if_exists=True)
    op.drop_index("idx_logs_requested_at_model_tier", table_name="request_logs", if_exists=True)
    op.drop_index("idx_api_keys_name", table_name="api_keys", if_exists=True)
    op.execute(sa.text("DROP INDEX IF EXISTS idx_usage_window_account_time"))
