"""persist immutable targets for retained HTTP bridge response aliases

Revision ID: 20260827_000000_add_http_bridge_retained_alias_target
Revises: 20260821_020000_add_http_bridge_replay_snapshot
Create Date: 2026-08-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260827_000000_add_http_bridge_retained_alias_target"
down_revision = "20260821_020000_add_http_bridge_replay_snapshot"
branch_labels = None
depends_on = None

_TABLE = "http_bridge_session_aliases"
_COLUMN = "target_response_id"
_created_column = False


def _columns(bind) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(_TABLE):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    global _created_column
    bind = op.get_bind()
    _created_column = False
    columns = _columns(bind)
    if not columns or _COLUMN in columns:
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.add_column(sa.Column(_COLUMN, sa.Text(), nullable=True))
    _created_column = True


def downgrade() -> None:
    bind = op.get_bind()
    if not _created_column or _COLUMN not in _columns(bind):
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_column(_COLUMN)
