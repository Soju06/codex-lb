"""add dashboard data-retention settings

Revision ID: 20260716_010000_add_dashboard_retention_settings
Revises: 20260716_000000_add_oauth_device_flow_slots
Create Date: 2026-07-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260716_010000_add_dashboard_retention_settings"
down_revision = "20260716_000000_add_oauth_device_flow_slots"
branch_labels = None
depends_on = None

_COLUMN_NAMES = ("request_log_retention_days", "usage_history_retention_days")


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    dashboard_columns = _columns(bind, "dashboard_settings")
    if not dashboard_columns:
        return
    with op.batch_alter_table("dashboard_settings") as batch_op:
        for name in _COLUMN_NAMES:
            if name not in dashboard_columns:
                batch_op.add_column(sa.Column(name, sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    dashboard_columns = _columns(bind, "dashboard_settings")
    if not dashboard_columns:
        return
    with op.batch_alter_table("dashboard_settings") as batch_op:
        for name in reversed(_COLUMN_NAMES):
            if name in dashboard_columns:
                batch_op.drop_column(name)
