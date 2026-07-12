"""add account usage rollups table and fold-state row

Revision ID: 20260712_010000_add_account_usage_rollups
Revises: 20260711_030000_add_limit_warmup_idle_threshold
Create Date: 2026-07-12
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "20260712_010000_add_account_usage_rollups"
down_revision = "20260711_030000_add_limit_warmup_idle_threshold"
branch_labels = None
depends_on = None

_ROLLUPS_TABLE = "account_usage_rollups"
_STATE_TABLE = "account_usage_rollup_state"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(_ROLLUPS_TABLE):
        op.create_table(
            _ROLLUPS_TABLE,
            sa.Column("account_id", sa.String(), nullable=False),
            sa.Column("request_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.Column("input_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.Column("output_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.Column("cached_input_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.Column("total_cost_usd", sa.Float(), server_default=sa.text("0"), nullable=False),
            sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("account_id"),
        )
    if not inspector.has_table(_STATE_TABLE):
        state_table = op.create_table(
            _STATE_TABLE,
            sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
            sa.Column("folded_through", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        # Seed the watermark row so fold passes always have a row to lock.
        op.bulk_insert(state_table, [{"id": 1, "folded_through": datetime(1970, 1, 1)}])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table(_STATE_TABLE):
        op.drop_table(_STATE_TABLE)
    if inspector.has_table(_ROLLUPS_TABLE):
        op.drop_table(_ROLLUPS_TABLE)
