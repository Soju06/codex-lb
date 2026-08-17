"""add source scope to sticky continuity abandonment

Revision ID: 20260812_120000_add_sticky_abandonment_scope
Revises: 20260813_000000_add_file_account_pins
Create Date: 2026-08-12 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260812_120000_add_sticky_abandonment_scope"
down_revision = "20260813_000000_add_file_account_pins"
branch_labels = None
depends_on = None

_TABLE = "sticky_sessions"
_COLUMN = "continuity_abandonment_scope"


def _columns(connection: Connection) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(_TABLE):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(_TABLE) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    if _COLUMN in _columns(bind):
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.add_column(sa.Column(_COLUMN, sa.String(length=32), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN not in _columns(bind):
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_column(_COLUMN)
