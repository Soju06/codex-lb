"""Repair recovery columns on databases stamped before the recovery branch was deployed."""

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
_COLUMN_SPECS = (
    ("rebind_claim_id", sa.String(36), None),
    ("transcript_version", sa.Integer(), sa.text("0")),
    ("response_output_items_json", sa.Text(), None),
    ("response_output_items_complete", sa.Boolean(), sa.text("false")),
    ("response_replay_input_json", sa.Text(), None),
    ("response_replay_input_complete", sa.Boolean(), sa.text("false")),
    ("response_replay_input_turn_count", sa.Integer(), sa.text("0")),
)


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
        for name, column_type, server_default in _COLUMN_SPECS:
            if name in columns:
                continue
            if server_default is None:
                column = sa.Column(name, column_type, nullable=True)
            else:
                column = sa.Column(name, column_type, nullable=False, server_default=server_default)
            batch_op.add_column(column)
            created_columns.append(name)
    for name in created_columns:
        mark_created(bind, revision, "column", name)


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind)
    if not columns:
        return
    columns_to_drop = [
        name for name, _, _ in _COLUMN_SPECS if name in columns and was_created(bind, revision, "column", name)
    ]
    if columns_to_drop:
        with op.batch_alter_table(_TABLE) as batch_op:
            for name in reversed(columns_to_drop):
                batch_op.drop_column(name)
        for name in columns_to_drop:
            forget_created(bind, revision, "column", name)
    drop_ownership_table_if_empty(bind)
