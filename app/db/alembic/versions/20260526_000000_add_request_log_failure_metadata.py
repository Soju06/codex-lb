"""add request log failure metadata

Revision ID: 20260526_000000_add_request_log_failure_metadata
Revises: 20260525_000000_add_usage_raw_window_latest_index
Create Date: 2026-05-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260526_000000_add_request_log_failure_metadata"
down_revision = "20260601_000000_merge_relative_availability_and_usage_raw_heads"
branch_labels = None
depends_on = None

_REQUEST_LOGS_TABLE = "request_logs"


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
    _add_column_if_missing(
        bind,
        _REQUEST_LOGS_TABLE,
        "failure_phase",
        sa.Column("failure_phase", sa.String(), nullable=True),
    )
    _add_column_if_missing(
        bind,
        _REQUEST_LOGS_TABLE,
        "failure_detail",
        sa.Column("failure_detail", sa.Text(), nullable=True),
    )
    _add_column_if_missing(
        bind,
        _REQUEST_LOGS_TABLE,
        "failure_exception_type",
        sa.Column("failure_exception_type", sa.String(), nullable=True),
    )
    _add_column_if_missing(
        bind,
        _REQUEST_LOGS_TABLE,
        "upstream_status_code",
        sa.Column("upstream_status_code", sa.Integer(), nullable=True),
    )
    _add_column_if_missing(
        bind,
        _REQUEST_LOGS_TABLE,
        "upstream_error_code",
        sa.Column("upstream_error_code", sa.String(), nullable=True),
    )
    _add_column_if_missing(
        bind,
        _REQUEST_LOGS_TABLE,
        "bridge_stage",
        sa.Column("bridge_stage", sa.String(), nullable=True),
    )


def downgrade() -> None:
    bind = op.get_bind()
    for column_name in (
        "bridge_stage",
        "upstream_error_code",
        "upstream_status_code",
        "failure_exception_type",
        "failure_detail",
        "failure_phase",
    ):
        _drop_column_if_present(bind, _REQUEST_LOGS_TABLE, column_name)
