"""add priorities enabled to dashboard settings

Revision ID: 20260503_000100_add_dashboard_priorities_enabled
Revises: 20260503_000000_add_accounts_priority
Create Date: 2026-05-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260503_000100_add_dashboard_priorities_enabled"
down_revision = "20260503_000000_add_accounts_priority"
branch_labels = None
depends_on = None


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if not columns or "priorities_enabled" in columns:
        return

    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.add_column(
            sa.Column(
                "priorities_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if "priorities_enabled" not in columns:
        return

    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.drop_column("priorities_enabled")
