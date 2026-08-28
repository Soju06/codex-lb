"""add API-key reasoning effort allowlists

The nullable column preserves the existing unrestricted policy for every
existing API key. New writes serialize a non-empty canonical JSON list.

Revision ID: 20260806_030000_add_api_key_allowed_reasoning_efforts
Revises: 20260816_000000_add_account_pending_deletion
Create Date: 2026-08-06 03:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260806_030000_add_api_key_allowed_reasoning_efforts"
down_revision = "20260816_000000_add_account_pending_deletion"
branch_labels = None
depends_on = None

_TABLE = "api_keys"
_COLUMN = "allowed_reasoning_efforts"
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
    if _COLUMN not in columns:
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.add_column(sa.Column(_COLUMN, sa.Text(), nullable=True))

    constraints = _check_constraints(connection)
    if _POLICY_CHECK not in constraints:
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.create_check_constraint(
                _POLICY_CHECK,
                f"{_COLUMN} IS NULL OR enforced_reasoning_effort IS NULL",
            )


def downgrade() -> None:
    connection = op.get_bind()
    columns = _columns(connection)
    if _COLUMN not in columns:
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        if _POLICY_CHECK in _check_constraints(connection):
            batch_op.drop_constraint(_POLICY_CHECK, type_="check")
        batch_op.drop_column(_COLUMN)
