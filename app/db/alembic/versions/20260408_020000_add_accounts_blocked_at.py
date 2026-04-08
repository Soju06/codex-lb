"""add blocked_at to accounts

Revision ID: 20260408_020000_add_accounts_blocked_at
Revises: 20260408_010000_merge_import_without_overwrite_and_assignment_heads
Create Date: 2026-04-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260408_020000_add_accounts_blocked_at"
down_revision = "20260408_010000_merge_import_without_overwrite_and_assignment_heads"
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
    if not columns or "blocked_at" in columns:
        return

    with op.batch_alter_table("accounts") as batch_op:
        batch_op.add_column(sa.Column("blocked_at", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "accounts")
    if "blocked_at" not in columns:
        return

    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_column("blocked_at")
