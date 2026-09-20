"""Add the optional estimated subscription usage-share cap to API keys.

Revision ID: 20260918_000000_add_api_key_usage_share_percent
Revises: 20260914_000002_merge_overflow_retirement_heads
Create Date: 2026-09-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260918_000000_add_api_key_usage_share_percent"
down_revision = "20260914_000002_merge_overflow_retirement_heads"
branch_labels = None
depends_on = None

_TABLE = "api_keys"
_COLUMN = "usage_share_percent"
_CONSTRAINT = "ck_api_keys_usage_share_percent"
_ROLLUP_TABLE = "request_demand_quarter_rollups"
_ROLLUP_INDEX = "idx_request_demand_account_slot"
_ROLLUP_INDEX_COLUMNS = ("account_id", "slot_epoch")


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_TABLE):
        return set()
    return {column["name"] for column in inspector.get_columns(_TABLE)}


def _constraints() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_TABLE):
        return set()
    return {constraint["name"] for constraint in inspector.get_check_constraints(_TABLE) if constraint["name"]}


def _index_columns(table_name: str, index_name: str) -> tuple[str, ...] | None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return None
    for index in inspector.get_indexes(table_name):
        if index.get("name") == index_name:
            return tuple(str(column) for column in index.get("column_names") or ())
    return None


def _create_rollup_index() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_ROLLUP_TABLE):
        return
    existing_columns = _index_columns(_ROLLUP_TABLE, _ROLLUP_INDEX)
    wrong_shape = existing_columns is not None and existing_columns != _ROLLUP_INDEX_COLUMNS
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            invalid = bind.execute(
                sa.text(
                    "SELECT 1 FROM pg_index i "
                    "JOIN pg_class c ON c.oid = i.indexrelid "
                    "JOIN pg_class t ON t.oid = i.indrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = current_schema() AND c.relname = :name "
                    "AND t.relname = :table AND NOT i.indisvalid"
                ),
                {"name": _ROLLUP_INDEX, "table": _ROLLUP_TABLE},
            ).scalar()
            if invalid or wrong_shape:
                op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {_ROLLUP_INDEX}"))
            op.execute(
                sa.text(
                    f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_ROLLUP_INDEX} "
                    f"ON {_ROLLUP_TABLE} ({', '.join(_ROLLUP_INDEX_COLUMNS)})"
                )
            )
        return
    if wrong_shape:
        op.drop_index(_ROLLUP_INDEX, table_name=_ROLLUP_TABLE)
        existing_columns = None
    if existing_columns is None:
        op.create_index(_ROLLUP_INDEX, _ROLLUP_TABLE, list(_ROLLUP_INDEX_COLUMNS))


def _drop_rollup_index() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {_ROLLUP_INDEX}"))
        return
    op.drop_index(_ROLLUP_INDEX, table_name=_ROLLUP_TABLE, if_exists=True)


def upgrade() -> None:
    if _COLUMN not in _columns():
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Integer(), nullable=True))
    if _CONSTRAINT not in _constraints():
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.create_check_constraint(
                _CONSTRAINT,
                f"{_COLUMN} IS NULL OR ({_COLUMN} >= 1 AND {_COLUMN} <= 100)",
            )
    _create_rollup_index()


def downgrade() -> None:
    _drop_rollup_index()
    if _CONSTRAINT in _constraints():
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_constraint(_CONSTRAINT, type_="check")
    if _COLUMN in _columns():
        op.drop_column(_TABLE, _COLUMN)
