"""add account refresh claims coordination table

Revision ID: 20260713_040000_add_account_refresh_claims
Revises: 20260713_020000_add_model_registry_snapshot
Create Date: 2026-07-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260713_040000_add_account_refresh_claims"
down_revision = "20260713_020000_add_model_registry_snapshot"
branch_labels = None
depends_on = None

_TABLE_NAME = "account_refresh_claims"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table(_TABLE_NAME):
        return
    op.create_table(
        _TABLE_NAME,
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("claimed_by", sa.String(length=128), nullable=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=False),
        sa.Column("claim_expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(_TABLE_NAME):
        return
    op.drop_table(_TABLE_NAME)
