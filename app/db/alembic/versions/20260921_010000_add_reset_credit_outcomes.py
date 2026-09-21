"""Retain reset-credit outcome evidence separately from request pins."""

import sqlalchemy as sa
from alembic import op

revision = "20260921_010000_add_reset_credit_outcomes"
down_revision = "20260910_010000_merge_beta6_and_key_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("reset_credit_redeem_requests") as batch:
        batch.add_column(sa.Column("credit_expires_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("origin", sa.String(), nullable=False, server_default="legacy"))
        batch.add_column(sa.Column("outcome", sa.String(), nullable=False, server_default="unknown"))
        batch.add_column(sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("updated_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("next_retry_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("upstream_code", sa.String()))
        batch.add_column(sa.Column("windows_reset", sa.Integer()))
        batch.add_column(sa.Column("redeemed_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("usage_verified", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("reset_credit_redeem_requests") as batch:
        for name in (
            "usage_verified",
            "redeemed_at",
            "windows_reset",
            "upstream_code",
            "next_retry_at",
            "updated_at",
            "attempt_count",
            "outcome",
            "origin",
            "credit_expires_at",
        ):
            batch.drop_column(name)
