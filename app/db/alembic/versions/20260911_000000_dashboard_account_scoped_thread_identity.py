"""add the dashboard-managed account-scoped thread identity switch

One nullable ``dashboard_settings`` BOOLEAN column named after the ``Settings``
field it takes over: ``account_scoped_thread_identity_enabled``. NULL means
"inherit": the ``CODEX_LB_ACCOUNT_SCOPED_THREAD_IDENTITY_ENABLED`` environment
alias, then the code default (``False``), keep applying until an operator sets
a value in the dashboard. The environment value is never copied into the
column (configuration-tiers: the environment is a fallback, never a seed).

Revision ID: 20260911_000000_dashboard_account_scoped_thread_identity
Revises: 20260910_020000_add_dashboard_role_mappings
Create Date: 2026-09-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260911_000000_dashboard_account_scoped_thread_identity"
down_revision = "20260910_020000_add_dashboard_role_mappings"
branch_labels = None
depends_on = None

_SETTINGS_TABLE = "dashboard_settings"
_COLUMN_NAME = "account_scoped_thread_identity_enabled"


def _column_names(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = _column_names(bind, _SETTINGS_TABLE)
    if existing_columns and _COLUMN_NAME not in existing_columns:
        with op.batch_alter_table(_SETTINGS_TABLE) as batch_op:
            batch_op.add_column(sa.Column(_COLUMN_NAME, sa.Boolean(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN_NAME in _column_names(bind, _SETTINGS_TABLE):
        with op.batch_alter_table(_SETTINGS_TABLE) as batch_op:
            batch_op.drop_column(_COLUMN_NAME)
