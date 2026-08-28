"""add account-owned Codex installation id

Revision ID: 20260613_000000_add_accounts_codex_installation_id
Revises: 20260607_000000_merge_weekly_monthly_useragent_heads
Create Date: 2026-06-13
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260613_000000_add_accounts_codex_installation_id"
down_revision = "20260607_000000_merge_weekly_monthly_useragent_heads"
branch_labels = None
depends_on = None

_TABLE = "accounts"
_COLUMN = "codex_installation_id"


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, _TABLE)
    if not columns:
        return
    if _COLUMN not in columns:
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.add_column(sa.Column(_COLUMN, sa.String(), nullable=True))

    rows = bind.execute(sa.text(f"SELECT id FROM {_TABLE} WHERE {_COLUMN} IS NULL OR {_COLUMN} = ''")).fetchall()
    for row in rows:
        bind.execute(
            sa.text(f"UPDATE {_TABLE} SET {_COLUMN} = :installation_id WHERE id = :account_id"),
            {"installation_id": str(uuid.uuid4()), "account_id": row[0]},
        )

    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.alter_column(_COLUMN, existing_type=sa.String(), nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN not in _columns(bind, _TABLE):
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_column(_COLUMN)
