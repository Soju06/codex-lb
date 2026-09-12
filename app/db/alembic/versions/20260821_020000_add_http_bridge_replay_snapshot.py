"""persist bounded self-contained HTTP bridge replay snapshots

Revision ID: 20260821_020000_add_http_bridge_replay_snapshot
Revises: 20260821_010000_add_http_bridge_complete_transcript
Create Date: 2026-08-21
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

revision = "20260821_020000_add_http_bridge_replay_snapshot"
down_revision = "20260821_010000_add_http_bridge_complete_transcript"
branch_labels = None
depends_on = None

_TABLE = "http_bridge_operations"


def _columns(bind) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(_TABLE):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind)
    if not columns:
        return
    ensure_ownership_table(bind)
    created_columns: list[str] = []
    with op.batch_alter_table(_TABLE) as batch_op:
        if "response_replay_input_json" not in columns:
            created_columns.append("response_replay_input_json")
            batch_op.add_column(sa.Column("response_replay_input_json", sa.Text(), nullable=True))
        if "response_replay_input_complete" not in columns:
            created_columns.append("response_replay_input_complete")
            batch_op.add_column(
                sa.Column(
                    "response_replay_input_complete",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                )
            )
    for column in created_columns:
        mark_created(bind, revision, "column", column)


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind)
    columns_to_drop = [
        column
        for column in ("response_replay_input_complete", "response_replay_input_json")
        if column in columns and was_created(bind, revision, "column", column)
    ]
    if columns_to_drop:
        with op.batch_alter_table(_TABLE) as batch_op:
            for column in columns_to_drop:
                batch_op.drop_column(column)
        for column in columns_to_drop:
            forget_created(bind, revision, "column", column)
    drop_ownership_table_if_empty(bind)
