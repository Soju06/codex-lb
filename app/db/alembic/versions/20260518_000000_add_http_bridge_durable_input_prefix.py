"""add HTTP bridge durable input prefix metadata

Revision ID: 20260518_000000_add_http_bridge_durable_input_prefix
Revises: 20260515_000000_soft_delete_request_logs_on_account_delete
Create Date: 2026-05-18 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision: str = "20260518_000000_add_http_bridge_durable_input_prefix"
down_revision: str | Sequence[str] | None = "20260515_000000_soft_delete_request_logs_on_account_delete"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = _columns(bind, "http_bridge_sessions")
    with op.batch_alter_table("http_bridge_sessions") as batch_op:
        if "latest_input_item_count" not in existing_columns:
            batch_op.add_column(sa.Column("latest_input_item_count", sa.Integer(), nullable=True))
        if "latest_input_full_fingerprint" not in existing_columns:
            batch_op.add_column(
                sa.Column("latest_input_full_fingerprint", sa.String(length=64), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    existing_columns = _columns(bind, "http_bridge_sessions")
    with op.batch_alter_table("http_bridge_sessions") as batch_op:
        if "latest_input_full_fingerprint" in existing_columns:
            batch_op.drop_column("latest_input_full_fingerprint")
        if "latest_input_item_count" in existing_columns:
            batch_op.drop_column("latest_input_item_count")
