"""Repair recovery schema objects on databases stamped before the recovery branch was deployed."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.alembic.http_bridge_migration_ownership import (
    ensure_ownership_table,
    mark_created,
)

revision = "20260911_070000_repair_http_bridge_recovery_columns"
down_revision = "20260911_060000_add_bridge_session_continuity_abandonment"
branch_labels = None
depends_on = None

_TABLE = "http_bridge_operations"
_ALIAS_TABLE = "http_bridge_session_aliases"
_ALIAS_COLUMN = "target_response_id"
_INDEX_SPECS = (
    ("idx_http_bridge_operations_session_state_created", ("session_id", "state", "created_at")),
    ("idx_http_bridge_operations_response_state", ("response_id", "state")),
)
_COLUMN_SPECS = (
    ("rebind_claim_id", sa.String(36), None),
    ("transcript_version", sa.Integer(), sa.text("0")),
    ("response_output_items_json", sa.Text(), None),
    ("response_output_items_complete", sa.Boolean(), sa.text("false")),
    ("response_replay_input_json", sa.Text(), None),
    ("response_replay_input_complete", sa.Boolean(), sa.text("false")),
    ("response_replay_input_turn_count", sa.Integer(), sa.text("0")),
)

# These objects are owned by the historical additive revisions below. A
# database stamped past those revisions can be missing their DDL when this
# repair runs, but downgrading the repair must still leave the parent schema
# intact. Record the historical owner so those revisions retain ownership if
# a later downgrade reaches them.
_COLUMN_OWNER_REVISIONS = {
    "rebind_claim_id": "20260906_000000_add_http_bridge_rebind_claim",
    "transcript_version": "20260821_010000_add_http_bridge_complete_transcript",
    "response_output_items_json": "20260821_010000_add_http_bridge_complete_transcript",
    "response_output_items_complete": "20260821_010000_add_http_bridge_complete_transcript",
    "response_replay_input_json": "20260821_020000_add_http_bridge_replay_snapshot",
    "response_replay_input_complete": "20260821_020000_add_http_bridge_replay_snapshot",
    "response_replay_input_turn_count": "20260828_010000_add_http_bridge_replay_turn_count",
}
_INDEX_OWNER_REVISIONS = {
    "idx_http_bridge_operations_session_state_created": "20260815_000000_add_http_bridge_recent_unknown_index",
    "idx_http_bridge_operations_response_state": "20260821_010000_add_http_bridge_complete_transcript",
}
_ALIAS_COLUMN_OWNER_REVISION = "20260827_000000_add_http_bridge_retained_alias_target"


def _columns(bind, table: str) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table)}


def _indexes(bind, table: str) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table):
        return set()
    return {str(index["name"]) for index in inspector.get_indexes(table) if index.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    operation_columns = _columns(bind, _TABLE)
    alias_columns = _columns(bind, _ALIAS_TABLE)
    if not operation_columns and not alias_columns:
        return

    ensure_ownership_table(bind)
    created_columns: list[str] = []
    created_indexes: list[str] = []
    if operation_columns:
        with op.batch_alter_table(_TABLE) as batch_op:
            for name, column_type, server_default in _COLUMN_SPECS:
                if name in operation_columns:
                    continue
                if server_default is None:
                    column = sa.Column(name, column_type, nullable=True)
                else:
                    column = sa.Column(name, column_type, nullable=False, server_default=server_default)
                batch_op.add_column(column)
                created_columns.append(name)
        existing_indexes = _indexes(bind, _TABLE)
        for index_name, index_columns in _INDEX_SPECS:
            if index_name in existing_indexes:
                continue
            op.create_index(index_name, _TABLE, list(index_columns), unique=False)
            created_indexes.append(index_name)
    if alias_columns and _ALIAS_COLUMN not in alias_columns:
        with op.batch_alter_table(_ALIAS_TABLE) as batch_op:
            batch_op.add_column(sa.Column(_ALIAS_COLUMN, sa.Text(), nullable=True))
        mark_created(bind, _ALIAS_COLUMN_OWNER_REVISION, "column", _ALIAS_COLUMN)
    for name in created_columns:
        mark_created(bind, _COLUMN_OWNER_REVISIONS.get(name, revision), "column", name)
    for name in created_indexes:
        mark_created(bind, _INDEX_OWNER_REVISIONS.get(name, revision), "index", name)


def downgrade() -> None:
    """Preserve parent-owned objects restored by this compatibility repair.

    The repaired objects belong to migrations at or below the parent stamp.
    Some databases also carry the old repair's revision-local ownership
    markers, so consulting those markers here could still delete persisted
    columns and indexes. Leave the schema untouched on downgrade.
    """
