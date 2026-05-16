"""add request_logs response lookup index

Revision ID: 20260415_160000_add_request_logs_response_lookup_index
Revises: 20260413_000000_add_accounts_blocked_at
Create Date: 2026-04-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260415_160000_add_request_logs_response_lookup_index"
down_revision = "20260413_000000_add_accounts_blocked_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_columns = {column["name"] for column in inspector.get_columns("request_logs")}
    if "session_id" not in existing_columns:
        op.add_column("request_logs", sa.Column("session_id", sa.String(), nullable=True))

    op.create_index(
        "idx_logs_request_status_api_key_time",
        "request_logs",
        [
            "request_id",
            "status",
            "api_key_id",
            sa.text("requested_at DESC"),
            sa.text("id DESC"),
        ],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "idx_logs_request_status_api_key_session_time",
        "request_logs",
        [
            "request_id",
            "status",
            "api_key_id",
            "session_id",
            sa.text("requested_at DESC"),
            sa.text("id DESC"),
        ],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_logs_request_status_api_key_session_time",
        table_name="request_logs",
        if_exists=True,
    )
    op.drop_index(
        "idx_logs_request_status_api_key_time",
        table_name="request_logs",
        if_exists=True,
    )
    op.drop_column("request_logs", "session_id")
