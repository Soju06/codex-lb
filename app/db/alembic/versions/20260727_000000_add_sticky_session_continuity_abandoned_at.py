"""add sticky session continuity_abandoned_at

Revision ID: 20260727_000000_add_sticky_session_continuity_abandoned_at
Revises: 20260803_000000_merge_http_bridge_recovery_and_capability_lineage_heads
Create Date: 2026-07-27 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260727_000000_add_sticky_session_continuity_abandoned_at"
down_revision = "20260726_000000_add_account_plan_downgrade_observations"
branch_labels = None
depends_on = None

_STICKY_SESSIONS_TABLE = "sticky_sessions"
_COLUMN_NAME = "continuity_abandoned_at"


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_STICKY_SESSIONS_TABLE):
        return
    if _COLUMN_NAME not in _columns(bind, _STICKY_SESSIONS_TABLE):
        with op.batch_alter_table(_STICKY_SESSIONS_TABLE) as batch_op:
            batch_op.add_column(sa.Column(_COLUMN_NAME, sa.DateTime(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_STICKY_SESSIONS_TABLE):
        return
    if _COLUMN_NAME in _columns(bind, _STICKY_SESSIONS_TABLE):
        with op.batch_alter_table(_STICKY_SESSIONS_TABLE) as batch_op:
            batch_op.drop_column(_COLUMN_NAME)
