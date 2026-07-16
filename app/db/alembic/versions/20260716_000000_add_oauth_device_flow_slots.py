"""add oauth device-flow single-active-slot coordination table

Revision ID: 20260716_000000_add_oauth_device_flow_slots
Revises: 20260714_000000_add_oauth_flow_states
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260716_000000_add_oauth_device_flow_slots"
down_revision = "20260714_000000_add_oauth_flow_states"
branch_labels = None
depends_on = None

_TABLE_NAME = "oauth_device_flow_slots"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table(_TABLE_NAME):
        return
    op.create_table(
        _TABLE_NAME,
        sa.Column("slot_key", sa.String(), nullable=False),
        sa.Column("flow_id", sa.String(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("slot_key"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(_TABLE_NAME):
        return
    op.drop_table(_TABLE_NAME)
