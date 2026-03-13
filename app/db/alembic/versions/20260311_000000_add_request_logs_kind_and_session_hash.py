"""add request kind and session hash to request_logs

Revision ID: 20260311_000000_add_request_logs_kind_and_session_hash
Revises: 20260312_120000_add_dashboard_upstream_stream_transport
Create Date: 2026-03-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260311_000000_add_request_logs_kind_and_session_hash"
down_revision = "20260312_120000_add_dashboard_upstream_stream_transport"
branch_labels = None
depends_on = None


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "request_logs")
    if not columns:
        return

    with op.batch_alter_table("request_logs") as batch_op:
        if "request_kind" not in columns:
            batch_op.add_column(sa.Column("request_kind", sa.String(), nullable=True))
        if "session_id_hash" not in columns:
            batch_op.add_column(sa.Column("session_id_hash", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "request_logs")
    if not columns:
        return

    with op.batch_alter_table("request_logs") as batch_op:
        if "session_id_hash" in columns:
            batch_op.drop_column("session_id_hash")
        if "request_kind" in columns:
            batch_op.drop_column("request_kind")
