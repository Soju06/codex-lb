"""add single-account routing setting

Revision ID: 20260531_000000_add_single_account_routing
Revises: 20260601_000000_merge_relative_availability_and_usage_raw_heads
Create Date: 2026-05-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260531_000000_add_single_account_routing"
down_revision = "20260601_000000_merge_relative_availability_and_usage_raw_heads"
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
    if not columns or "single_account_id" in columns:
        return
    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.add_column(sa.Column("single_account_id", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if "single_account_id" not in columns:
        return
    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.drop_column("single_account_id")
