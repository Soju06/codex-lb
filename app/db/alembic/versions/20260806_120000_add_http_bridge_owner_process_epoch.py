"""add owner process epoch to durable HTTP bridge sessions

Revision ID: 20260806_120000_add_http_bridge_owner_process_epoch
Revises: 20260808_000000_tune_usage_history_autovacuum
Create Date: 2026-08-06 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260806_120000_add_http_bridge_owner_process_epoch"
down_revision = "20260808_000000_tune_usage_history_autovacuum"
branch_labels = None
depends_on = None

_TABLE = "http_bridge_sessions"
_COLUMN = "owner_process_epoch"


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
        batch_op.add_column(sa.Column(_COLUMN, sa.String(length=64), nullable=True))
    op.drop_index("idx_http_bridge_sessions_owner_state", table_name=_TABLE, if_exists=True)
    op.create_index(
        "idx_http_bridge_sessions_owner_state",
        _TABLE,
        ["owner_instance_id", _COLUMN, "state"],
        if_not_exists=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN not in _columns(bind):
        return
    op.drop_index("idx_http_bridge_sessions_owner_state", table_name=_TABLE, if_exists=True)
    op.create_index(
        "idx_http_bridge_sessions_owner_state",
        _TABLE,
        ["owner_instance_id", "state"],
        if_not_exists=True,
    )
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_column(_COLUMN)
