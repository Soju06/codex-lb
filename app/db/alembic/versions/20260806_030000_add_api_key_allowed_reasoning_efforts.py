"""add API-key reasoning effort allowlists

The nullable column preserves the existing unrestricted policy for every
existing API key. New writes serialize a non-empty canonical JSON list.

Revision ID: 20260806_030000_add_api_key_allowed_reasoning_efforts
Revises: 20260806_020000_add_usage_history_bulk_covering_indexes
Create Date: 2026-08-06 03:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260806_030000_add_api_key_allowed_reasoning_efforts"
down_revision = "20260806_020000_add_usage_history_bulk_covering_indexes"
branch_labels = None
depends_on = None

_TABLE = "api_keys"
_COLUMN = "allowed_reasoning_efforts"


def _columns(connection: Connection) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(_TABLE):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(_TABLE) if column.get("name") is not None}


def upgrade() -> None:
    if _COLUMN in _columns(op.get_bind()):
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.add_column(sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    if _COLUMN not in _columns(op.get_bind()):
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_column(_COLUMN)
