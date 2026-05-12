"""add dashboard session ttl setting

Revision ID: 20260421_000000_add_dashboard_session_ttl_setting
Revises: 20260417_000000_add_request_log_plan_type
Create Date: 2026-04-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260421_000000_add_dashboard_session_ttl_setting"
down_revision = "20260417_000000_add_request_log_plan_type"
branch_labels = None
depends_on = None


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if not columns or "dashboard_session_ttl_seconds" in columns:
        return

    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.add_column(
            sa.Column(
                "dashboard_session_ttl_seconds",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("43200"),
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if not columns or "dashboard_session_ttl_seconds" not in columns:
        return

    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.drop_column("dashboard_session_ttl_seconds")
