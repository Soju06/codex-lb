"""add request_logs api_key account index for account-cost breakdown

Revision ID: 20260520_010000_add_request_logs_api_key_account_index
Revises: 20260520_000000_merge_api_key_and_http_bridge_heads
Create Date: 2026-05-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260520_010000_add_request_logs_api_key_account_index"
down_revision = "20260520_000000_merge_api_key_and_http_bridge_heads"
branch_labels = None
depends_on = None

_INDEX_NAME = "idx_logs_api_key_time_account"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("request_logs"):
        return

    existing_indexes = {index["name"] for index in inspector.get_indexes("request_logs")}
    if _INDEX_NAME not in existing_indexes:
        op.create_index(
            _INDEX_NAME,
            "request_logs",
            ["api_key_id", sa.text("requested_at DESC"), "account_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("request_logs"):
        return

    existing_indexes = {index["name"] for index in inspector.get_indexes("request_logs")}
    if _INDEX_NAME in existing_indexes:
        op.drop_index(_INDEX_NAME, table_name="request_logs")
