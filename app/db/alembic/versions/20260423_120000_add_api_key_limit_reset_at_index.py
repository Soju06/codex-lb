"""add api_key_limits reset_at index

Revision ID: 20260423_120000_add_api_key_limit_reset_at_index
Revises: 20260421_120000_merge_request_log_lookup_and_plan_type_heads
Create Date: 2026-04-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260423_120000_add_api_key_limit_reset_at_index"
down_revision = "20260421_120000_merge_request_log_lookup_and_plan_type_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("api_key_limits"):
        return

    existing_indexes = {index["name"] for index in inspector.get_indexes("api_key_limits")}
    if "idx_api_key_limits_reset_at" not in existing_indexes:
        op.create_index(
            "idx_api_key_limits_reset_at",
            "api_key_limits",
            ["reset_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("api_key_limits"):
        return

    existing_indexes = {index["name"] for index in inspector.get_indexes("api_key_limits")}
    if "idx_api_key_limits_reset_at" in existing_indexes:
        op.drop_index("idx_api_key_limits_reset_at", table_name="api_key_limits")
