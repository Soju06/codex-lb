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
    inspector = sa.inspect(bind)
    if inspector.has_table(_TABLE_NAME):
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
    # If the account-rollup fold already advanced the shared watermark (an
    # install that ran the account-rollup change before this one), per-key
    # reads would aggregate only the live tail while this table starts
    # empty - collapsing every key's lifetime totals. Reset the fold state
    # (rollup rows + watermark together, per the documented escape hatch) so
    # the next fold pass re-backfills BOTH rollups from raw request_logs;
    # summaries stay correct throughout via the full live-tail fallback.
    if inspector.has_table("account_usage_rollup_state"):
        op.execute(sa.text("DELETE FROM account_usage_rollups"))
        op.execute(sa.text("UPDATE account_usage_rollup_state SET folded_through = '1970-01-01 00:00:00'"))


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE_NAME):
        return
    op.drop_table(_TABLE_NAME)
