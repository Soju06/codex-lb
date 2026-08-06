"""add connection-scoped request kind to request logs

Revision ID: 20260804_230000_add_request_log_connection_request_kind
Revises: 20260727_000000_add_sticky_session_continuity_abandoned_at
Create Date: 2026-08-04 23:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260804_230000_add_request_log_connection_request_kind"
down_revision = "20260727_000000_add_sticky_session_continuity_abandoned_at"
branch_labels = None
depends_on = None

_TABLE = "request_logs"
_COLUMN = "connection_request_kind"


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
        batch_op.add_column(sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN not in _columns(bind):
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_column(_COLUMN)
