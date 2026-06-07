"""add request log useragent fields

Revision ID: 20260602_070000_add_request_log_useragent_fields
Revises: 20260602_060000_merge_account_workspace_and_failure_heads
Create Date: 2026-06-02 07:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260602_070000_add_request_log_useragent_fields"
down_revision = "20260602_060000_merge_account_workspace_and_failure_heads"
branch_labels = None
depends_on = None

_REQUEST_LOGS_TABLE = "request_logs"
_USERAGENT_GROUP_INDEX = "idx_logs_useragent_group"


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def _add_column_if_missing(
    connection: Connection,
    table_name: str,
    column_name: str,
    column: sa.Column,
) -> None:
    columns = _columns(connection, table_name)
    if not columns or column_name in columns:
        return
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.add_column(column)


def _drop_column_if_present(connection: Connection, table_name: str, column_name: str) -> None:
    columns = _columns(connection, table_name)
    if not columns or column_name not in columns:
        return
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.drop_column(column_name)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(_REQUEST_LOGS_TABLE):
        return

    _add_column_if_missing(
        bind,
        _REQUEST_LOGS_TABLE,
        "useragent",
        sa.Column("useragent", sa.Text(), nullable=True),
    )
    _add_column_if_missing(
        bind,
        _REQUEST_LOGS_TABLE,
        "useragent_group",
        sa.Column("useragent_group", sa.String(), nullable=True),
    )

    existing_indexes = {index["name"] for index in inspector.get_indexes(_REQUEST_LOGS_TABLE)}
    if _USERAGENT_GROUP_INDEX not in existing_indexes:
        op.create_index(_USERAGENT_GROUP_INDEX, _REQUEST_LOGS_TABLE, ["useragent_group"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(_REQUEST_LOGS_TABLE):
        return

    existing_indexes = {index["name"] for index in inspector.get_indexes(_REQUEST_LOGS_TABLE)}
    if _USERAGENT_GROUP_INDEX in existing_indexes:
        op.drop_index(_USERAGENT_GROUP_INDEX, table_name=_REQUEST_LOGS_TABLE)

    _drop_column_if_present(bind, _REQUEST_LOGS_TABLE, "useragent_group")
    _drop_column_if_present(bind, _REQUEST_LOGS_TABLE, "useragent")
