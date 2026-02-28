"""add reset_at to accounts

Revision ID: 20260213_000300_add_accounts_reset_at
Revises: 20260213_000200_add_request_logs_reasoning_effort
Create Date: 2026-02-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260213_000300_add_accounts_reset_at"
down_revision = "20260213_000200_add_request_logs_reasoning_effort"
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
    if not columns or "reset_at" in columns:
        return

    with op.batch_alter_table("accounts") as batch_op:
        batch_op.add_column(sa.Column("reset_at", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "accounts")
    if "reset_at" not in columns:
        return

    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_column("reset_at")
