"""add dashboard columns for upstream timeouts and request budgets

Seven nullable ``dashboard_settings`` columns named after the ``Settings``
fields they override (slop-removal C2-1): ``upstream_connect_timeout_seconds``,
``proxy_request_budget_seconds``, ``compact_request_budget_seconds``,
``transcription_request_budget_seconds``, ``stream_idle_timeout_seconds``,
``proxy_downstream_websocket_idle_timeout_seconds`` and
``sse_keepalive_interval_seconds``. NULL means "inherit the environment value
(or the code default)"; the environment is never copied into the column.

Revision ID: 20260909_040000_dashboard_timeout_settings
Revises: 20260909_030000_dashboard_resilience_toggle_settings
Create Date: 2026-09-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260909_040000_dashboard_timeout_settings"
down_revision = "20260909_030000_dashboard_resilience_toggle_settings"
branch_labels = None
depends_on = None

_SETTINGS_TABLE = "dashboard_settings"
_COLUMN_NAMES = (
    "upstream_connect_timeout_seconds",
    "proxy_request_budget_seconds",
    "compact_request_budget_seconds",
    "transcription_request_budget_seconds",
    "stream_idle_timeout_seconds",
    "proxy_downstream_websocket_idle_timeout_seconds",
    "sse_keepalive_interval_seconds",
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
