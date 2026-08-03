"""Drop legacy split pending tool call columns.

Revision ID: 20260729_000000_drop_legacy_bridge_pending_tool_columns
Revises: 20260728_000000_merge_security_lineage_and_pending_tool_calls_heads
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260729_000000_drop_legacy_bridge_pending_tool_columns"
down_revision = "20260728_000000_merge_security_lineage_and_pending_tool_calls_heads"
branch_labels = None
depends_on = None

_TABLE = "http_bridge_sessions"
_CURRENT_COLUMN = "latest_pending_tool_calls_json"
_LEGACY_COLUMNS = (
    "latest_pending_function_call_ids",
    "latest_pending_custom_tool_call_ids",
)


def _columns(connection: Connection) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(_TABLE):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(_TABLE) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind)
    if not columns:
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        if _CURRENT_COLUMN not in columns:
            batch_op.add_column(sa.Column(_CURRENT_COLUMN, sa.Text(), nullable=True))
        for column in _LEGACY_COLUMNS:
            if column in columns:
                batch_op.drop_column(column)


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind)
    if not columns:
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        for column in _LEGACY_COLUMNS:
            if column not in columns:
                batch_op.add_column(sa.Column(column, sa.Text(), nullable=True))
