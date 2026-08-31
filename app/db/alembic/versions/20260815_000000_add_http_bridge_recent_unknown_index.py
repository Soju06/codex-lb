"""index bounded HTTP bridge UNKNOWN recovery lookups

Revision ID: 20260815_000000_add_http_bridge_recent_unknown_index
Revises: 20260828_010000_add_http_bridge_replay_turn_count
Create Date: 2026-08-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260815_000000_add_http_bridge_recent_unknown_index"
down_revision = "20260828_010000_add_http_bridge_replay_turn_count"
branch_labels = None
depends_on = None

_TABLE = "http_bridge_operations"
_INDEX = "idx_http_bridge_operations_session_state_created"


def _has_table(connection: Connection) -> bool:
    return sa.inspect(connection).has_table(_TABLE)


def _has_index(connection: Connection) -> bool:
    return any(index.get("name") == _INDEX for index in sa.inspect(connection).get_indexes(_TABLE))


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind) and not _has_index(bind):
        op.create_index(_INDEX, _TABLE, ["session_id", "state", "created_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind) and _has_index(bind):
        op.drop_index(_INDEX, table_name=_TABLE)
