"""add weekly_usage_cap_pct to accounts

Revision ID: 20260813_000000_add_accounts_weekly_usage_cap
Revises: 20260806_000000_add_anonymous_telemetry
Create Date: 2026-08-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260813_000000_add_accounts_weekly_usage_cap"
down_revision = "20260806_000000_add_anonymous_telemetry"
branch_labels = None
depends_on = None


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "accounts")
    if not columns or "weekly_usage_cap_pct" in columns:
        return

    with op.batch_alter_table("accounts") as batch_op:
        batch_op.add_column(sa.Column("weekly_usage_cap_pct", sa.Float(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "accounts")
    if "weekly_usage_cap_pct" not in columns:
        return

    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_column("weekly_usage_cap_pct")
