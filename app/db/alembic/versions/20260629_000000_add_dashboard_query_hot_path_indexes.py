"""Add hot-path indexes for dashboard and additional quota reads.

Revision ID: 20260629_000000_add_dashboard_query_hot_path_indexes
Revises: 20260626_010000_add_request_logs_upstream_transport
Create Date: 2026-06-29 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260629_000000_add_dashboard_query_hot_path_indexes"
down_revision = "20260626_010000_add_request_logs_upstream_transport"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_additional_usage_quota_window_latest",
        "additional_usage_history",
        [
            "quota_key",
            sa.text('"window"'),
            "account_id",
            sa.text("recorded_at DESC"),
            sa.text("used_percent DESC"),
            sa.text("id DESC"),
        ],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "idx_logs_account_kind_deleted_latest",
        "request_logs",
        ["account_id", "request_kind", "deleted_at", "requested_at", "id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "idx_logs_account_request_latest",
        "request_logs",
        ["account_id", "request_id", "requested_at", "id"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("idx_logs_account_request_latest", table_name="request_logs", if_exists=True)
    op.drop_index("idx_logs_account_kind_deleted_latest", table_name="request_logs", if_exists=True)
    op.drop_index("ix_additional_usage_quota_window_latest", table_name="additional_usage_history", if_exists=True)
