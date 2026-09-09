"""Persist reset-credit intent and recovery before upstream consumption."""

import sqlalchemy as sa
from alembic import op

revision = "20260909_120000_add_reset_credit_attempts"
down_revision = "20260729_000000_add_federation_usage_daily"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reset_credit_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("active_slot", sa.Integer(), nullable=True, unique=True),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("credit_id", sa.String(), nullable=True),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("result_code", sa.String(30), nullable=True),
        sa.Column("windows_reset", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(36), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("reset_credit_attempts")
