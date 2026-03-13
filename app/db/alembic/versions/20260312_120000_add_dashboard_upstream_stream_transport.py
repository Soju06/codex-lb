"""add dashboard upstream stream transport setting

Revision ID: 20260312_120000_add_dashboard_upstream_stream_transport
Revises: 20260312_000000_split_sticky_sessions_primary_key_by_kind
Create Date: 2026-03-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260312_120000_add_dashboard_upstream_stream_transport"
down_revision = "20260313_024500_merge_request_log_transport_heads"
branch_labels = None
depends_on = None


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "dashboard_settings"):
        return
    if "upstream_stream_transport" in _columns(bind, "dashboard_settings"):
        return

    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.add_column(
            sa.Column(
                "upstream_stream_transport",
                sa.String(),
                nullable=False,
                server_default=sa.text("'default'"),
            )
        )

    bind.execute(
        sa.text(
            """
            UPDATE dashboard_settings
            SET upstream_stream_transport = 'default'
            WHERE upstream_stream_transport IS NULL OR upstream_stream_transport = ''
            """
        )
    )


def downgrade() -> None:
    return
