"""add account concurrency overrides

Revision ID: 20260718_000000_add_account_concurrency_overrides
Revises: 20260716_010000_add_dashboard_retention_settings
Create Date: 2026-07-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260718_000000_add_account_concurrency_overrides"
down_revision = "20260716_010000_add_dashboard_retention_settings"
branch_labels = None
depends_on = None

_COLUMN_NAMES = ("response_create_limit_override", "stream_limit_override")


def _columns(connection: Connection) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table("accounts"):
        return set()
    return {str(column["name"]) for column in inspector.get_columns("accounts") if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    account_columns = _columns(bind)
    if not account_columns:
        return
    with op.batch_alter_table("accounts") as batch_op:
        for name in _COLUMN_NAMES:
            if name not in account_columns:
                batch_op.add_column(sa.Column(name, sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    account_columns = _columns(bind)
    if not account_columns:
        return
    with op.batch_alter_table("accounts") as batch_op:
        for name in reversed(_COLUMN_NAMES):
            if name in account_columns:
                batch_op.drop_column(name)
