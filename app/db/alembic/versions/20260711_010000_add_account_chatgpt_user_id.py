"""add accounts.chatgpt_user_id for per-seat identity

Revision ID: 20260711_010000_add_account_chatgpt_user_id
Revises: 20260711_000000_add_dashboard_prohibit_fast_mode
Create Date: 2026-07-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260711_010000_add_account_chatgpt_user_id"
down_revision = "20260711_000000_add_dashboard_prohibit_fast_mode"
branch_labels = None
depends_on = None

_TABLE = "accounts"
_COLUMN = "chatgpt_user_id"


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    existing = _columns(op.get_bind(), _TABLE)
    if existing and _COLUMN not in existing:
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.add_column(sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    if _COLUMN in _columns(op.get_bind(), _TABLE):
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_column(_COLUMN)
