"""add dashboard columns for the stream and session bridge request budgets

Two nullable ``dashboard_settings`` columns named after the ``Settings``
fields they override (slop-removal M1):
``http_responses_stream_request_budget_seconds`` and
``http_responses_session_bridge_request_budget_seconds``. NULL means "inherit
the environment value (or the code default)"; the environment is never copied
into the column.

Revision ID: 20260909_080000_dashboard_stream_bridge_budgets
Revises: 20260909_100000_dashboard_codex_prewarm
Create Date: 2026-09-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260909_080000_dashboard_stream_bridge_budgets"
down_revision = "20260909_100000_dashboard_codex_prewarm"
branch_labels = None
depends_on = None

_SETTINGS_TABLE = "dashboard_settings"
_COLUMN_NAMES = (
    "http_responses_stream_request_budget_seconds",
    "http_responses_session_bridge_request_budget_seconds",
)


def _settings_columns() -> tuple[sa.Column, ...]:
    # Fresh Column objects per call: a Column binds to the table it is added to.
    return tuple(sa.Column(name, sa.Float(), nullable=True) for name in _COLUMN_NAMES)


def _column_names(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = _column_names(bind, _SETTINGS_TABLE)
    missing_columns = [column for column in _settings_columns() if column.name not in existing_columns]
    if existing_columns and missing_columns:
        with op.batch_alter_table(_SETTINGS_TABLE) as batch_op:
            for column in missing_columns:
                batch_op.add_column(column)


def downgrade() -> None:
    bind = op.get_bind()
    existing_columns = _column_names(bind, _SETTINGS_TABLE)
    present_columns = [name for name in _COLUMN_NAMES if name in existing_columns]
    if present_columns:
        with op.batch_alter_table(_SETTINGS_TABLE) as batch_op:
            for column_name in present_columns:
                batch_op.drop_column(column_name)
