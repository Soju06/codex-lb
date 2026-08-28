"""add request log conversation id

Revision ID: 20260720_000000_add_request_log_conversation_id
Revises: 20260717_000000_optimize_dashboard_hot_path_indexes
Create Date: 2026-07-20 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260720_000000_add_request_log_conversation_id"
down_revision = "20260717_000000_optimize_dashboard_hot_path_indexes"
branch_labels = None
depends_on = None

_REQUEST_LOGS_TABLE = "request_logs"
_CONVERSATION_ID_INDEX = "idx_logs_conversation_id"


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

    if "conversation_id" not in _columns(bind, _REQUEST_LOGS_TABLE):
        with op.batch_alter_table(_REQUEST_LOGS_TABLE) as batch_op:
            batch_op.add_column(sa.Column("conversation_id", sa.String(), nullable=True))

    existing_indexes = {index["name"] for index in inspector.get_indexes(_REQUEST_LOGS_TABLE)}
    if _CONVERSATION_ID_INDEX not in existing_indexes:
        op.create_index(_CONVERSATION_ID_INDEX, _REQUEST_LOGS_TABLE, ["conversation_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(_REQUEST_LOGS_TABLE):
        return

    existing_indexes = {index["name"] for index in inspector.get_indexes(_REQUEST_LOGS_TABLE)}
    if _CONVERSATION_ID_INDEX in existing_indexes:
        op.drop_index(_CONVERSATION_ID_INDEX, table_name=_REQUEST_LOGS_TABLE)

    if "conversation_id" in _columns(bind, _REQUEST_LOGS_TABLE):
        with op.batch_alter_table(_REQUEST_LOGS_TABLE) as batch_op:
            batch_op.drop_column("conversation_id")
