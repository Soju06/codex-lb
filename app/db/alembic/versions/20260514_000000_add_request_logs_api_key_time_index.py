"""add request_logs api_key time index

Revision ID: 20260514_000000_add_request_logs_api_key_time_index
Revises: 20260424_000000_merge_dashboard_session_ttl_and_request_log_heads
Create Date: 2026-05-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260514_000000_add_request_logs_api_key_time_index"
down_revision = "20260424_000000_merge_dashboard_session_ttl_and_request_log_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("request_logs"):
        return

    existing_indexes = {index["name"] for index in inspector.get_indexes("request_logs")}
    if "idx_logs_api_key_time" not in existing_indexes:
        op.create_index(
            "idx_logs_api_key_time",
            "request_logs",
            ["api_key_id", sa.text("requested_at DESC"), sa.text("id DESC")],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("request_logs"):
        return

    existing_indexes = {index["name"] for index in inspector.get_indexes("request_logs")}
    if "idx_logs_api_key_time" in existing_indexes:
        op.drop_index("idx_logs_api_key_time", table_name="request_logs")
