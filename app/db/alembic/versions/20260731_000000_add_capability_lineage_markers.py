"""add durable capability lineage markers

Revision ID: 20260731_000000_add_capability_lineage_markers
Revises: 20260725_000000_add_http_bridge_pending_tool_calls
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260731_000000_add_capability_lineage_markers"
down_revision = "20260725_000000_add_http_bridge_pending_tool_calls"
branch_labels = None
depends_on = None

_TABLE = "capability_lineage_markers"


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table(_TABLE):
        return
    op.create_table(
        _TABLE,
        sa.Column("marker_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("marker_hash"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE):
        return
    op.drop_table(_TABLE)
