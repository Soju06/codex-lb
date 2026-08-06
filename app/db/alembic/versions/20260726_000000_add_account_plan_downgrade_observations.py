"""add account plan downgrade observations table

Revision ID: 20260726_000000_add_account_plan_downgrade_observations
Revises: 20260803_000000_merge_http_bridge_recovery_and_capability_lineage_heads
Create Date: 2026-07-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260726_000000_add_account_plan_downgrade_observations"
down_revision = "20260803_000000_merge_http_bridge_recovery_and_capability_lineage_heads"
branch_labels = None
depends_on = None

_TABLE_NAME = "account_plan_downgrade_observations"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table(_TABLE_NAME):
        return
    op.create_table(
        _TABLE_NAME,
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("observations", sa.Integer(), nullable=False),
        sa.Column("credential_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("observed_plan_type", sa.String(), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(_TABLE_NAME):
        return
    op.drop_table(_TABLE_NAME)
