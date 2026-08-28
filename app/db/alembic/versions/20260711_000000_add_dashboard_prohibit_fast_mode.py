"""add dashboard Fast Mode prohibition setting

Revision ID: 20260711_000000_add_dashboard_prohibit_fast_mode
Revises: 20260709_000000_add_ttft_phase_observability
Create Date: 2026-07-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260711_000000_add_dashboard_prohibit_fast_mode"
down_revision = "20260709_000000_add_ttft_phase_observability"
branch_labels = None
depends_on = None


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    dashboard_columns = _columns(bind, "dashboard_settings")
    if dashboard_columns and "prohibit_fast_mode" not in dashboard_columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.add_column(
                sa.Column("prohibit_fast_mode", sa.Boolean(), nullable=False, server_default=sa.false())
            )


def downgrade() -> None:
    bind = op.get_bind()
    dashboard_columns = _columns(bind, "dashboard_settings")
    if "prohibit_fast_mode" in dashboard_columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.drop_column("prohibit_fast_mode")
