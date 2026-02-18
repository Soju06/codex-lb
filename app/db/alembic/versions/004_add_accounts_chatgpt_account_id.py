"""add chatgpt_account_id to accounts

Revision ID: 004_add_accounts_chatgpt_account_id
Revises: 003_add_accounts_reset_at
Create Date: 2026-02-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "004_add_accounts_chatgpt_account_id"
down_revision = "003_add_accounts_reset_at"
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
    if not columns or "chatgpt_account_id" in columns:
        return

    with op.batch_alter_table("accounts") as batch_op:
        batch_op.add_column(sa.Column("chatgpt_account_id", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "accounts")
    if "chatgpt_account_id" not in columns:
        return

    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_column("chatgpt_account_id")
