"""Add dashboard_user_invites (one-time invitation links for pre-created accounts).

Revision ID: 20260909_040000_add_dashboard_user_invites
Revises: 20260909_030000_add_audit_actor_columns
Create Date: 2026-09-09

Only the token hash is stored. One live invite per user (``user_id`` unique);
the row is removed with its user (``ON DELETE CASCADE``). ``created_by_user_id``
is a snapshot without a foreign key so the invite survives the inviter's
deletion. No data backfill: invites only exist from this release on.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260909_040000_add_dashboard_user_invites"
down_revision = "20260909_030000_add_audit_actor_columns"
branch_labels = None
depends_on = None

_TABLE = "dashboard_user_invites"


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table(_TABLE):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("dashboard_users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("sso_only", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("username_locked", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table(_TABLE):
        op.drop_table(_TABLE)
