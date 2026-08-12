"""persist the HTTP bridge ambiguous recovery dispatch budget

Revision ID: 20260810_000000_add_http_bridge_recovery_dispatch_count
Revises: 20260807_000000_merge_http_bridge_operation_spool_and_latest_main
Create Date: 2026-08-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260810_000000_add_http_bridge_recovery_dispatch_count"
down_revision = "20260807_000000_merge_http_bridge_operation_spool_and_latest_main"
branch_labels = None
depends_on = None

_TABLE = "http_bridge_operations"
_COLUMN = "recovery_dispatch_count"


def _has_table(connection: Connection) -> bool:
    return sa.inspect(connection).has_table(_TABLE)


def _has_column(connection: Connection) -> bool:
    return any(item["name"] == _COLUMN for item in sa.inspect(connection).get_columns(_TABLE))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind) or _has_column(bind):
        return
    op.add_column(
        _TABLE,
        sa.Column(_COLUMN, sa.Integer(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind) and _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)
