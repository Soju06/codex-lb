"""add request-log queue-wait latency

Revision ID: 20260715_000000_add_request_log_queue_latency
Revises: 20260713_040000_add_account_refresh_claims
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260715_000000_add_request_log_queue_latency"
down_revision = "20260713_040000_add_account_refresh_claims"
branch_labels = None
depends_on = None

_TABLE = "request_logs"
_COLUMN = "latency_queue_ms"


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, _TABLE)
    if not columns or _COLUMN in columns:
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.add_column(sa.Column(_COLUMN, sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN not in _columns(bind, _TABLE):
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_column(_COLUMN)
