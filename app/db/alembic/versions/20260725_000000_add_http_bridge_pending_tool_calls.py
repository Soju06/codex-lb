"""add HTTP bridge durable pending tool call manifest

Revision ID: 20260725_000000_add_http_bridge_pending_tool_calls
Revises: 20260724_000000_add_request_usage_time_rollups
Create Date: 2026-07-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260725_000000_add_http_bridge_pending_tool_calls"
down_revision = "20260724_000000_add_request_usage_time_rollups"
branch_labels = None
depends_on = None

_TABLE = "http_bridge_sessions"
_COLUMN = "latest_pending_tool_calls_json"


def _columns(connection: Connection) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(_TABLE):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(_TABLE) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    if _COLUMN in _columns(bind):
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.add_column(sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN not in _columns(bind):
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_column(_COLUMN)
