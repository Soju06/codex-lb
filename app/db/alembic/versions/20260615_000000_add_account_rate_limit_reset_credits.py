"""add account rate limit reset credits

Revision ID: 20260615_000000_add_account_rate_limit_reset_credits
Revises: 20260611_000000_merge_dashboard_guest_and_weekly_useragent_heads
Create Date: 2026-06-15 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260615_000000_add_account_rate_limit_reset_credits"
down_revision = "20260611_000000_merge_dashboard_guest_and_weekly_useragent_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("account_rate_limit_reset_credits"):
        op.create_table(
            "account_rate_limit_reset_credits",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("account_id", sa.String(), nullable=False),
            sa.Column("credit_id", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("granted_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("redeemed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "account_id",
                "credit_id",
                name="uq_account_rate_limit_reset_credits_account_credit",
            ),
        )

    index_names = {index["name"] for index in inspector.get_indexes("account_rate_limit_reset_credits")}
    if "idx_account_rate_limit_reset_credits_account_id" not in index_names:
        op.create_index(
            "idx_account_rate_limit_reset_credits_account_id",
            "account_rate_limit_reset_credits",
            ["account_id"],
            unique=False,
        )
    if "idx_account_rate_limit_reset_credits_status" not in index_names:
        op.create_index(
            "idx_account_rate_limit_reset_credits_status",
            "account_rate_limit_reset_credits",
            ["status"],
            unique=False,
        )
    if "idx_account_rate_limit_reset_credits_expires_at" not in index_names:
        op.create_index(
            "idx_account_rate_limit_reset_credits_expires_at",
            "account_rate_limit_reset_credits",
            ["expires_at"],
            unique=False,
        )
    if "idx_account_rate_limit_reset_credits_account_status_expires_at" not in index_names:
        op.create_index(
            "idx_account_rate_limit_reset_credits_account_status_expires_at",
            "account_rate_limit_reset_credits",
            ["account_id", "status", "expires_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("account_rate_limit_reset_credits"):
        return

    index_names = {index["name"] for index in inspector.get_indexes("account_rate_limit_reset_credits")}
    if "idx_account_rate_limit_reset_credits_account_status_expires_at" in index_names:
        op.drop_index(
            "idx_account_rate_limit_reset_credits_account_status_expires_at",
            table_name="account_rate_limit_reset_credits",
        )
    if "idx_account_rate_limit_reset_credits_expires_at" in index_names:
        op.drop_index("idx_account_rate_limit_reset_credits_expires_at", table_name="account_rate_limit_reset_credits")
    if "idx_account_rate_limit_reset_credits_status" in index_names:
        op.drop_index("idx_account_rate_limit_reset_credits_status", table_name="account_rate_limit_reset_credits")
    if "idx_account_rate_limit_reset_credits_account_id" in index_names:
        op.drop_index("idx_account_rate_limit_reset_credits_account_id", table_name="account_rate_limit_reset_credits")
    op.drop_table("account_rate_limit_reset_credits")
