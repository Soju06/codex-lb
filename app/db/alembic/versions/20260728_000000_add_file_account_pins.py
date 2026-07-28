"""add durable file account pins

Revision ID: 20260728_000000_add_file_account_pins
Revises: 20260725_000000_add_http_bridge_pending_tool_calls
Create Date: 2026-07-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260728_000000_add_file_account_pins"
down_revision = "20260725_000000_add_http_bridge_pending_tool_calls"
branch_labels = None
depends_on = None

_TABLE = "file_account_pins"


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table(_TABLE):
        return
    op.create_table(
        _TABLE,
        sa.Column("file_id", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("file_id"),
    )
    op.create_index("ix_file_account_pins_expires_at", _TABLE, ["expires_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE):
        return
    op.drop_index("ix_file_account_pins_expires_at", table_name=_TABLE)
    op.drop_table(_TABLE)
