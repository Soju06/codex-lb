"""add dashboard-managed HTTP bridge operation spool retention column

One nullable ``dashboard_settings`` FLOAT column named after the ``Settings``
field it takes over:
``http_responses_session_bridge_operation_spool_retention_seconds``. NULL means
"inherit": the deprecated
``CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_OPERATION_SPOOL_RETENTION_SECONDS``
environment alias, then the code default (7 days), keep applying until an
operator sets a value in the dashboard's data retention card. The environment
value is never copied into the column (configuration-tiers; slop-removal
campaign 0908, R2).

Revision ID: 20260910_010000_dashboard_spool_retention
Revises: 20260910_000000_request_logs_missing_cost_index
Create Date: 2026-09-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260910_010000_dashboard_spool_retention"
down_revision = "20260910_000000_request_logs_missing_cost_index"
branch_labels = None
depends_on = None

_SETTINGS_TABLE = "dashboard_settings"
_COLUMN_NAME = "http_responses_session_bridge_operation_spool_retention_seconds"


def _column_names(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = _column_names(bind, _SETTINGS_TABLE)
    if existing_columns and _COLUMN_NAME not in existing_columns:
        with op.batch_alter_table(_SETTINGS_TABLE) as batch_op:
            batch_op.add_column(sa.Column(_COLUMN_NAME, sa.Float(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN_NAME in _column_names(bind, _SETTINGS_TABLE):
        with op.batch_alter_table(_SETTINGS_TABLE) as batch_op:
            batch_op.drop_column(_COLUMN_NAME)
