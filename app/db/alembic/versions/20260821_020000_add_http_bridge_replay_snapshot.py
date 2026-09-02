"""persist bounded self-contained HTTP bridge replay snapshots

Revision ID: 20260821_020000_add_http_bridge_replay_snapshot
Revises: 20260821_010000_add_http_bridge_complete_transcript
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260821_020000_add_http_bridge_replay_snapshot"
down_revision = "20260821_010000_add_http_bridge_complete_transcript"
branch_labels = None
depends_on = None

_TABLE = "http_bridge_operations"
_created_columns: set[str] = set()


def _columns(bind) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(_TABLE):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    global _created_columns
    bind = op.get_bind()
    _created_columns = set()
    columns = _columns(bind)
    if not columns:
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        if "response_replay_input_json" not in columns:
            _created_columns.add("response_replay_input_json")
            batch_op.add_column(sa.Column("response_replay_input_json", sa.Text(), nullable=True))
        if "response_replay_input_complete" not in columns:
            _created_columns.add("response_replay_input_complete")
            batch_op.add_column(
                sa.Column(
                    "response_replay_input_complete",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind)
    if not columns:
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        for column in ("response_replay_input_complete", "response_replay_input_json"):
            if column in _created_columns and column in columns:
                batch_op.drop_column(column)
