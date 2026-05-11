"""add api key omit priority request

Revision ID: 20260511_000000_add_api_key_omit_priority_request
Revises: 20260424_000000_merge_dashboard_session_ttl_and_request_log_heads
Create Date: 2026-05-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260511_000000_add_api_key_omit_priority_request"
down_revision = "20260424_000000_merge_dashboard_session_ttl_and_request_log_heads"
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
    if _table_exists(bind, "api_keys"):
        existing_api_key_columns = _columns(bind, "api_keys")
        with op.batch_alter_table("api_keys") as batch_op:
            if "omit_priority_request" not in existing_api_key_columns:
                batch_op.add_column(
                    sa.Column(
                        "omit_priority_request",
                        sa.Boolean(),
                        server_default=sa.false(),
                        nullable=False,
                    )
                )

    if _table_exists(bind, "request_logs"):
        existing_request_log_columns = _columns(bind, "request_logs")
        with op.batch_alter_table("request_logs") as batch_op:
            if "service_tier_omitted" not in existing_request_log_columns:
                batch_op.add_column(
                    sa.Column(
                        "service_tier_omitted",
                        sa.Boolean(),
                        server_default=sa.false(),
                        nullable=False,
                    )
                )


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "request_logs"):
        existing_request_log_columns = _columns(bind, "request_logs")
        with op.batch_alter_table("request_logs") as batch_op:
            if "service_tier_omitted" in existing_request_log_columns:
                batch_op.drop_column("service_tier_omitted")

    if _table_exists(bind, "api_keys"):
        existing_api_key_columns = _columns(bind, "api_keys")
        with op.batch_alter_table("api_keys") as batch_op:
            if "omit_priority_request" in existing_api_key_columns:
                batch_op.drop_column("omit_priority_request")
