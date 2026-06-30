"""add request log client ip

Revision ID: 20260611_010000_add_request_log_client_ip
Revises: 20260607_000000_merge_weekly_monthly_useragent_heads
Create Date: 2026-06-11 01:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260611_010000_add_request_log_client_ip"
down_revision = "20260607_000000_merge_weekly_monthly_useragent_heads"
branch_labels = None
depends_on = None

_REQUEST_LOGS_TABLE = "request_logs"
_CLIENT_IP_INDEX = "idx_logs_client_ip"


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(_REQUEST_LOGS_TABLE):
        return

    if "client_ip" not in _columns(bind, _REQUEST_LOGS_TABLE):
        with op.batch_alter_table(_REQUEST_LOGS_TABLE) as batch_op:
            batch_op.add_column(sa.Column("client_ip", sa.String(), nullable=True))

    existing_indexes = {index["name"] for index in inspector.get_indexes(_REQUEST_LOGS_TABLE)}
    if _CLIENT_IP_INDEX not in existing_indexes:
        op.create_index(_CLIENT_IP_INDEX, _REQUEST_LOGS_TABLE, ["client_ip"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(_REQUEST_LOGS_TABLE):
        return

    existing_indexes = {index["name"] for index in inspector.get_indexes(_REQUEST_LOGS_TABLE)}
    if _CLIENT_IP_INDEX in existing_indexes:
        op.drop_index(_CLIENT_IP_INDEX, table_name=_REQUEST_LOGS_TABLE)

    if "client_ip" in _columns(bind, _REQUEST_LOGS_TABLE):
        with op.batch_alter_table(_REQUEST_LOGS_TABLE) as batch_op:
            batch_op.drop_column("client_ip")
