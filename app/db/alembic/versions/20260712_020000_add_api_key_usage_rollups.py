"""add api key usage rollups table

Revision ID: 20260712_020000_add_api_key_usage_rollups
Revises: 20260712_010000_add_account_usage_rollups
Create Date: 2026-07-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260712_020000_add_api_key_usage_rollups"
down_revision = "20260712_010000_add_account_usage_rollups"
branch_labels = None
depends_on = None

_TABLE_NAME = "api_key_usage_rollups"


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table(_TABLE_NAME):
        return
    op.create_table(
        _TABLE_NAME,
        sa.Column("api_key_id", sa.String(), nullable=False),
        sa.Column("request_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("cached_input_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("total_cost_usd", sa.Float(), server_default=sa.text("0"), nullable=False),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("api_key_id"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE_NAME):
        return
    op.drop_table(_TABLE_NAME)
