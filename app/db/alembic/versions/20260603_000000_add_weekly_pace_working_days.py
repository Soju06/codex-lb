"""add weekly pace working days setting

Revision ID: 20260603_000000_add_weekly_pace_working_days
Revises: 20260604_000000_add_reauth_required_account_status
Create Date: 2026-06-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260603_000000_add_weekly_pace_working_days"
down_revision = "20260604_000000_add_reauth_required_account_status"
branch_labels = None
depends_on = None

_COLUMN_NAME = "weekly_pace_working_days"
_DEFAULT_WORKING_DAYS = "'0,1,2,3,4,5,6'"


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if not columns or _COLUMN_NAME in columns:
        return
    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.add_column(
            sa.Column(
                _COLUMN_NAME,
                sa.String(),
                nullable=False,
                server_default=sa.text(_DEFAULT_WORKING_DAYS),
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if _COLUMN_NAME not in columns:
        return
    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.drop_column(_COLUMN_NAME)
