"""add API-key reasoning effort allowlists

The nullable column preserves the existing unrestricted policy for every
existing API key. New writes serialize a non-empty canonical JSON list.
The schema version column makes legacy writers fail closed during rolling
upgrades instead of silently dropping a requested policy.

Revision ID: 20260806_030000_add_api_key_allowed_reasoning_efforts
Revises: 20260806_020000_add_usage_history_bulk_covering_indexes
Create Date: 2026-08-06 03:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260806_030000_add_api_key_allowed_reasoning_efforts"
down_revision = "20260806_020000_add_usage_history_bulk_covering_indexes"
branch_labels = None
depends_on = None

_TABLE = "api_keys"
_COLUMN = "allowed_reasoning_efforts"
_SCHEMA_VERSION_COLUMN = "api_key_policy_schema_version"
_SCHEMA_VERSION = 1
_POLICY_CHECK = "ck_api_keys_reasoning_policy_exclusive"


def _columns(connection: Connection) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(_TABLE):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(_TABLE) if column.get("name") is not None}


def _check_constraints(connection: Connection) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(_TABLE):
        return set()
    return {str(constraint["name"]) for constraint in inspector.get_check_constraints(_TABLE) if constraint.get("name")}


def upgrade() -> None:
    connection = op.get_bind()
    columns = _columns(connection)
    if _COLUMN not in columns or _SCHEMA_VERSION_COLUMN not in columns:
        with op.batch_alter_table(_TABLE) as batch_op:
            if _COLUMN not in columns:
                batch_op.add_column(sa.Column(_COLUMN, sa.Text(), nullable=True))
            if _SCHEMA_VERSION_COLUMN not in columns:
                batch_op.add_column(sa.Column(_SCHEMA_VERSION_COLUMN, sa.Integer(), nullable=True))

    op.execute(
        sa.text(
            f"UPDATE {_TABLE} SET {_SCHEMA_VERSION_COLUMN} = {_SCHEMA_VERSION} WHERE {_SCHEMA_VERSION_COLUMN} IS NULL"
        )
    )
    constraints = _check_constraints(connection)
    with op.batch_alter_table(_TABLE) as batch_op:
        if _POLICY_CHECK not in constraints:
            batch_op.create_check_constraint(
                _POLICY_CHECK,
                f"{_COLUMN} IS NULL OR enforced_reasoning_effort IS NULL",
            )
        batch_op.alter_column(_SCHEMA_VERSION_COLUMN, existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    connection = op.get_bind()
    columns = _columns(connection)
    if _COLUMN not in columns and _SCHEMA_VERSION_COLUMN not in columns:
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        if _POLICY_CHECK in _check_constraints(connection):
            batch_op.drop_constraint(_POLICY_CHECK, type_="check")
        if _SCHEMA_VERSION_COLUMN in columns:
            batch_op.drop_column(_SCHEMA_VERSION_COLUMN)
        if _COLUMN in columns:
            batch_op.drop_column(_COLUMN)
