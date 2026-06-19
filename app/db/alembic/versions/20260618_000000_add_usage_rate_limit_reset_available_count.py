"""add usage_history rate_limit_reset_available_count

Revision ID: 20260618_000000_add_usage_rate_limit_reset_available_count
Revises: 20260611_000000_merge_dashboard_guest_and_weekly_useragent_heads
Create Date: 2026-06-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260618_000000_add_usage_rate_limit_reset_available_count"
down_revision = "20260611_000000_merge_dashboard_guest_and_weekly_useragent_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("usage_history"):
        return

    columns = {column["name"] for column in inspector.get_columns("usage_history")}
    if "rate_limit_reset_available_count" not in columns:
        op.add_column(
            "usage_history",
            sa.Column("rate_limit_reset_available_count", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("usage_history"):
        return

    columns = {column["name"] for column in inspector.get_columns("usage_history")}
    if "rate_limit_reset_available_count" in columns:
        op.drop_column("usage_history", "rate_limit_reset_available_count")