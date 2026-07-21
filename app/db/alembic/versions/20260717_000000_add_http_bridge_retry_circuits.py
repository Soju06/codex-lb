"""add durable HTTP bridge retry circuit state

Revision ID: 20260717_000000_add_http_bridge_retry_circuits
Revises: 20260717_000000_optimize_dashboard_hot_path_indexes
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260717_000000_add_http_bridge_retry_circuits"
down_revision = "20260717_000000_optimize_dashboard_hot_path_indexes"
branch_labels = None
depends_on = None

_TABLE_NAME = "http_bridge_retry_circuits"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table(_TABLE_NAME):
        return
    op.create_table(
        _TABLE_NAME,
        sa.Column("session_key_kind", sa.String(length=64), nullable=False),
        sa.Column("session_key_hash", sa.String(length=64), nullable=False),
        sa.Column("api_key_scope", sa.String(length=255), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cooldown_until_epoch", sa.Float(), nullable=False, server_default="0"),
        sa.Column("last_detail", sa.String(length=255), nullable=True),
        sa.Column("updated_at_epoch", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint(
            "session_key_kind",
            "session_key_hash",
            "api_key_scope",
            name="pk_http_bridge_retry_circuits",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table(_TABLE_NAME):
        op.drop_table(_TABLE_NAME)
