"""add burn-rate columns to request_logs

Revision ID: 20260319_140000_add_request_logs_burn_rate_columns
Revises: 20260319_130000_add_burn_rate_history
Create Date: 2026-03-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260319_140000_add_request_logs_burn_rate_columns"
down_revision = "20260319_130000_add_burn_rate_history"
branch_labels = None
depends_on = None


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "request_logs")
    if not columns:
        return

    with op.batch_alter_table("request_logs") as batch_op:
        if "burn_rate_5h_plus_accounts" not in columns:
            batch_op.add_column(sa.Column("burn_rate_5h_plus_accounts", sa.Float(), nullable=True))
        if "burn_rate_7d_plus_accounts" not in columns:
            batch_op.add_column(sa.Column("burn_rate_7d_plus_accounts", sa.Float(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "request_logs")
    if not columns:
        return

    with op.batch_alter_table("request_logs") as batch_op:
        if "burn_rate_7d_plus_accounts" in columns:
            batch_op.drop_column("burn_rate_7d_plus_accounts")
        if "burn_rate_5h_plus_accounts" in columns:
            batch_op.drop_column("burn_rate_5h_plus_accounts")
