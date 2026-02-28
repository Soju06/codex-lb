"""add request_logs store_requested flag

Revision ID: 017_add_request_logs_store_requested
Revises: 016_add_response_context_storage
Create Date: 2026-02-28
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "017_add_request_logs_store_requested"
down_revision = "016_add_response_context_storage"
branch_labels = None
depends_on = None


def _column_names(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    try:
        columns = inspector.get_columns(table_name)
    except Exception:
        return set()
    return {str(column.get("name")) for column in columns if column.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _column_names(bind, "request_logs")
    with op.batch_alter_table("request_logs") as batch_op:
        if "store_requested" not in columns:
            batch_op.add_column(sa.Column("store_requested", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    bind = op.get_bind()
    columns = _column_names(bind, "request_logs")
    if "store_requested" in columns:
        with op.batch_alter_table("request_logs") as batch_op:
            batch_op.drop_column("store_requested")
