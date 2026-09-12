"""Repair recovery schema objects on databases stamped before the recovery branch was deployed."""

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

revision = "20260911_040000_repair_http_bridge_recovery_columns"
down_revision = "20260911_030000_add_local_login_policy"
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
        created_columns.append(_ALIAS_COLUMN)
    for name in created_columns:
        mark_created(bind, revision, "column", name)
    for name in created_indexes:
        mark_created(bind, revision, "index", name)


def downgrade() -> None:
    bind = op.get_bind()
    operation_columns = _columns(bind, _TABLE)
    operation_indexes = _indexes(bind, _TABLE)
    indexes_to_drop = [
        name for name, _ in _INDEX_SPECS if name in operation_indexes and was_created(bind, revision, "index", name)
    ]
    for name in indexes_to_drop:
        op.drop_index(name, table_name=_TABLE)
        forget_created(bind, revision, "index", name)
    columns_to_drop = [
        name
        for name, _, _ in _COLUMN_SPECS
        if name in operation_columns and was_created(bind, revision, "column", name)
    ]
    if columns_to_drop:
        with op.batch_alter_table(_TABLE) as batch_op:
            for name in reversed(columns_to_drop):
                batch_op.drop_column(name)
        for name in columns_to_drop:
            forget_created(bind, revision, "column", name)
    alias_columns = _columns(bind, _ALIAS_TABLE)
    if _ALIAS_COLUMN in alias_columns and was_created(bind, revision, "column", _ALIAS_COLUMN):
        with op.batch_alter_table(_ALIAS_TABLE) as batch_op:
            batch_op.drop_column(_ALIAS_COLUMN)
        forget_created(bind, revision, "column", _ALIAS_COLUMN)
    drop_ownership_table_if_empty(bind)
