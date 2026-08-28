"""add request log archive request id

Revision ID: 20260611_000000_add_request_log_archive_request_id
Revises: 20260607_000000_merge_weekly_monthly_useragent_heads
Create Date: 2026-06-11 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260611_000000_add_request_log_archive_request_id"
down_revision = "20260607_000000_merge_weekly_monthly_useragent_heads"
branch_labels = None
depends_on = None

_REQUEST_LOGS_TABLE = "request_logs"
_ARCHIVE_REQUEST_ID_COLUMN = "archive_request_id"


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, _REQUEST_LOGS_TABLE)
    if not columns or _ARCHIVE_REQUEST_ID_COLUMN in columns:
        return

    with op.batch_alter_table(_REQUEST_LOGS_TABLE) as batch_op:
        batch_op.add_column(sa.Column(_ARCHIVE_REQUEST_ID_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, _REQUEST_LOGS_TABLE)
    if not columns or _ARCHIVE_REQUEST_ID_COLUMN not in columns:
        return

    with op.batch_alter_table(_REQUEST_LOGS_TABLE) as batch_op:
        batch_op.drop_column(_ARCHIVE_REQUEST_ID_COLUMN)
