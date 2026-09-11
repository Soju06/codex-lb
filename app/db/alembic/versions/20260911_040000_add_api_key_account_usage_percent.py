"""Add the optional per-key local account usage share policy.

Revision ID: 20260911_040000_add_api_key_account_usage_percent
Revises: 20260911_030000_add_local_login_policy
Create Date: 2026-09-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260911_040000_add_api_key_account_usage_percent"
down_revision = "20260911_030000_add_local_login_policy"
branch_labels = None
depends_on = None

_TABLE = "api_keys"
_COLUMN = "account_usage_percent"


def _columns(connection: Connection) -> set[str]:
    if not sa.inspect(connection).has_table(_TABLE):
        return set()
    return {column["name"] for column in sa.inspect(connection).get_columns(_TABLE)}


def upgrade() -> None:
    if _COLUMN not in _columns(op.get_bind()):
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.add_column(sa.Column(_COLUMN, sa.Integer(), nullable=True))
            batch_op.create_check_constraint(
                "ck_api_keys_account_usage_percent_range",
                "account_usage_percent IS NULL OR (account_usage_percent >= 1 AND account_usage_percent <= 100)",
            )


def downgrade() -> None:
    if _COLUMN in _columns(op.get_bind()):
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_constraint("ck_api_keys_account_usage_percent_range", type_="check")
            batch_op.drop_column(_COLUMN)
