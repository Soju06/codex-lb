"""add bridge ring members table

Revision ID: 20260330_020000_add_bridge_ring_members
Revises: 20260330_010000_merge_scheduler_leader_and_cache_locality_heads
Create Date: 2026-03-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260330_020000_add_bridge_ring_members"
down_revision = "20260330_010000_merge_scheduler_leader_and_cache_locality_heads"
branch_labels = None
depends_on = None


def _has_table(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "bridge_ring_members"):
        return

    op.create_table(
        "bridge_ring_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("instance_id", sa.String(255), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.UniqueConstraint("instance_id", name="uq_bridge_ring_members_instance_id"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "bridge_ring_members"):
        return
    op.drop_table("bridge_ring_members")
