"""add relative availability routing settings to dashboard_settings

Revision ID: 20260426_000000_add_dashboard_relative_availability_settings
Revises: 20260423_120000_add_api_key_limit_reset_at_index
Create Date: 2026-04-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260426_000000_add_dashboard_relative_availability_settings"
down_revision = "20260423_120000_add_api_key_limit_reset_at_index"
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
    if not columns:
        return

    if "relative_availability_power" not in columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "relative_availability_power",
                    sa.Float(),
                    nullable=False,
                    server_default="2.0",
                )
            )

    if "relative_availability_top_k" not in columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "relative_availability_top_k",
                    sa.Integer(),
                    nullable=False,
                    server_default="5",
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if not columns:
        return

    if "relative_availability_top_k" in columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.drop_column("relative_availability_top_k")

    if "relative_availability_power" in columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.drop_column("relative_availability_power")
