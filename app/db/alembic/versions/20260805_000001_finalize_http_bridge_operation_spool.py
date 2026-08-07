"""make operation event spool completion conservative

Revision ID: 20260805_000001_finalize_http_bridge_operation_spool
Revises: 20260805_000000_add_http_bridge_operation_spool
Create Date: 2026-08-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260805_000001_finalize_http_bridge_operation_spool"
down_revision = "20260805_000000_add_http_bridge_operation_spool"
branch_labels = None
depends_on = None

_TABLE = "http_bridge_operations"


def _has_table(connection: Connection) -> bool:
    return sa.inspect(connection).has_table(_TABLE)


def _has_column(connection: Connection, column: str) -> bool:
    return any(item["name"] == column for item in sa.inspect(connection).get_columns(_TABLE))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind) or not _has_column(bind, "event_spool_complete"):
        return
    # Rows written by older releases used true as the implicit value. They
    # cannot be replayed safely unless their event queue is drained again.
    op.execute(sa.text(f"UPDATE {_TABLE} SET event_spool_complete = false"))
    # SQLite has no ALTER COLUMN syntax. The ORM model supplies the false
    # default for new rows there; PostgreSQL can update the catalog default.
    if bind.dialect.name != "sqlite":
        op.alter_column(_TABLE, "event_spool_complete", server_default=sa.text("false"))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite" and _has_table(bind) and _has_column(bind, "event_spool_complete"):
        op.alter_column(_TABLE, "event_spool_complete", server_default=sa.text("true"))
