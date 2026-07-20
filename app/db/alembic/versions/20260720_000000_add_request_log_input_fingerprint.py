"""add request log input fingerprint for durable continuity replay

Revision ID: 20260720_000000_add_request_log_input_fingerprint
Revises: 20260717_000000_optimize_dashboard_hot_path_indexes
Create Date: 2026-07-20 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision: str = "20260720_000000_add_request_log_input_fingerprint"
down_revision: str | Sequence[str] | None = "20260717_000000_optimize_dashboard_hot_path_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = _columns(bind, "request_logs")
    with op.batch_alter_table("request_logs") as batch_op:
        if "input_item_count" not in existing_columns:
            batch_op.add_column(sa.Column("input_item_count", sa.Integer(), nullable=True))
        if "input_full_fingerprint" not in existing_columns:
            batch_op.add_column(
                sa.Column("input_full_fingerprint", sa.String(length=64), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    existing_columns = _columns(bind, "request_logs")
    with op.batch_alter_table("request_logs") as batch_op:
        if "input_full_fingerprint" in existing_columns:
            batch_op.drop_column("input_full_fingerprint")
        if "input_item_count" in existing_columns:
            batch_op.drop_column("input_item_count")
