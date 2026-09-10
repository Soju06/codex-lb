"""persist the turn count represented by a complete HTTP bridge snapshot

Revision ID: 20260828_010000_add_http_bridge_replay_turn_count
Revises: 20260827_000000_add_http_bridge_retained_alias_target
Create Date: 2026-08-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.alembic.http_bridge_migration_ownership import (
    drop_ownership_table_if_empty,
    ensure_ownership_table,
    forget_created,
    mark_created,
    was_created,
)

revision = "20260828_010000_add_http_bridge_replay_turn_count"
down_revision = "20260827_000000_add_http_bridge_retained_alias_target"
branch_labels = None
depends_on = None

_TABLE = "http_bridge_operations"
_COLUMN = "response_replay_input_turn_count"


def _columns(bind) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(_TABLE):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind)
    if not columns or _COLUMN in columns:
        return
    ensure_ownership_table(bind)
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.add_column(sa.Column(_COLUMN, sa.Integer(), nullable=False, server_default=sa.text("0")))
    mark_created(bind, revision, "column", _COLUMN)


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN in _columns(bind) and was_created(bind, revision, "column", _COLUMN):
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_column(_COLUMN)
        forget_created(bind, revision, "column", _COLUMN)
    drop_ownership_table_if_empty(bind)
