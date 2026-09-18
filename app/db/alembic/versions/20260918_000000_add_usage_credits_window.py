"""add credits cap and spent on usage history

Revision ID: 20260918_000000_add_usage_credits_window
Revises: 20260909_120000_add_reset_credit_attempts
Create Date: 2026-09-18

Existing rows stay null. The credits window is balance plus cap, not a used percent.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260918_000000_add_usage_credits_window"
down_revision = "20260909_120000_add_reset_credit_attempts"
branch_labels = None
depends_on = None


def _add_columns() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("usage_history"):
        return
    columns = {column["name"] for column in inspector.get_columns("usage_history")}
    if "credits_cap" not in columns:
        op.add_column("usage_history", sa.Column("credits_cap", sa.Float(), nullable=True))
    if "credits_spent" not in columns:
        op.add_column("usage_history", sa.Column("credits_spent", sa.Float(), nullable=True))


def upgrade() -> None:
    _add_columns()


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("usage_history"):
        return
    columns = {column["name"] for column in inspector.get_columns("usage_history")}
    if "credits_spent" in columns:
        op.drop_column("usage_history", "credits_spent")
    if "credits_cap" in columns:
        op.drop_column("usage_history", "credits_cap")
