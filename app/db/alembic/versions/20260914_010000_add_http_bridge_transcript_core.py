"""Add the durable HTTP bridge transcript-core storage columns and indexes.

This is an expand-only release.  The columns are intentionally unwired: later
capture and recovery releases can roll out behind their own flags without
changing the request path in this migration.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260914_010000_add_http_bridge_transcript_core"
down_revision = "20260914_000001_merge_scim_and_subscription_heads"
branch_labels = None
depends_on = None

_TABLE = "http_bridge_operations"
_INDEXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "idx_http_bridge_operations_session_state_created",
        ("session_id", "state", "created_at"),
    ),
    ("idx_http_bridge_operations_response_state", ("response_id", "state")),
)


def _columns(connection: Connection) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(_TABLE):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(_TABLE)}


def _indexes(connection: Connection) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(_TABLE):
        return set()
    return {str(index["name"]) for index in inspector.get_indexes(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind)
    if not existing:
        return

    column_definitions = (
        sa.Column("transcript_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("response_output_items_json", sa.Text(), nullable=True),
        sa.Column(
            "response_output_items_complete",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("response_replay_input_json", sa.Text(), nullable=True),
        sa.Column(
            "response_replay_input_complete",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("response_replay_input_turn_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    missing = [column for column in column_definitions if column.name not in existing]
    if missing:
        with op.batch_alter_table(_TABLE) as batch_op:
            for column in missing:
                batch_op.add_column(column)

    present_indexes = _indexes(bind)
    for name, columns in _INDEXES:
        if name not in present_indexes:
            op.create_index(name, _TABLE, list(columns), unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if not _columns(bind):
        return

    present_indexes = _indexes(bind)
    for name, _columns_for_index in reversed(_INDEXES):
        if name in present_indexes:
            op.drop_index(name, table_name=_TABLE)

    columns = _columns(bind)
    to_drop = [
        name
        for name in (
            "response_replay_input_turn_count",
            "response_replay_input_complete",
            "response_replay_input_json",
            "response_output_items_complete",
            "response_output_items_json",
            "transcript_version",
        )
        if name in columns
    ]
    if to_drop:
        recreate = "never" if bind.dialect.name == "sqlite" else "auto"
        with op.batch_alter_table(_TABLE, recreate=recreate) as batch_op:
            for name in to_drop:
                batch_op.drop_column(name)
