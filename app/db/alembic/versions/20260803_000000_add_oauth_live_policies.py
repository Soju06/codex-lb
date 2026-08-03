"""add OAuth Live caller policies

Revision ID: 20260803_000000_add_oauth_live_policies
Revises: 20260731_000000_add_capability_lineage_markers
Create Date: 2026-08-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260803_000000_add_oauth_live_policies"
down_revision = "20260731_000000_add_capability_lineage_markers"
branch_labels = None
depends_on = None

_POLICIES_TABLE = "oauth_live_policies"
_ASSIGNMENTS_TABLE = "oauth_live_policy_accounts"
_ALLOWED_ACCOUNT_INDEX = "ix_oauth_live_policy_accounts_allowed_account_id"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(_POLICIES_TABLE):
        op.create_table(
            _POLICIES_TABLE,
            sa.Column("caller_account_id", sa.String(), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["caller_account_id"], ["accounts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("caller_account_id"),
        )

    inspector = sa.inspect(bind)
    if not inspector.has_table(_ASSIGNMENTS_TABLE):
        op.create_table(
            _ASSIGNMENTS_TABLE,
            sa.Column("caller_account_id", sa.String(), nullable=False),
            sa.Column("allowed_account_id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(
                ["caller_account_id"],
                ["oauth_live_policies.caller_account_id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(["allowed_account_id"], ["accounts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("caller_account_id", "allowed_account_id"),
        )
    assignment_indexes = (
        {str(index["name"]) for index in sa.inspect(bind).get_indexes(_ASSIGNMENTS_TABLE)}
        if sa.inspect(bind).has_table(_ASSIGNMENTS_TABLE)
        else set()
    )
    if _ALLOWED_ACCOUNT_INDEX not in assignment_indexes:
        op.create_index(
            _ALLOWED_ACCOUNT_INDEX,
            _ASSIGNMENTS_TABLE,
            ["allowed_account_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table(_ASSIGNMENTS_TABLE):
        indexes = {str(index["name"]) for index in inspector.get_indexes(_ASSIGNMENTS_TABLE)}
        if _ALLOWED_ACCOUNT_INDEX in indexes:
            op.drop_index(_ALLOWED_ACCOUNT_INDEX, table_name=_ASSIGNMENTS_TABLE)
        op.drop_table(_ASSIGNMENTS_TABLE)
    if sa.inspect(bind).has_table(_POLICIES_TABLE):
        op.drop_table(_POLICIES_TABLE)
