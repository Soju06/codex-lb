"""add api key usage sections

Revision ID: 20260601_010000_add_api_key_usage_sections
Revises: 20260629_000000_add_dashboard_query_hot_path_indexes
Create Date: 2026-06-01 01:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260601_010000_add_api_key_usage_sections"
down_revision = "20260629_000000_add_dashboard_query_hot_path_indexes"
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
    if not _table_exists(bind, "api_keys"):
        return

    existing_columns = _columns(bind, "api_keys")
    with op.batch_alter_table("api_keys") as batch_op:
        if "usage_sections" not in existing_columns:
            batch_op.add_column(
                sa.Column(
                    "usage_sections",
                    sa.Text(),
                    nullable=False,
                    server_default="upstream_limits,account_pool_usage",
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "api_keys"):
        return

    existing_columns = _columns(bind, "api_keys")
    with op.batch_alter_table("api_keys") as batch_op:
        if "usage_sections" in existing_columns:
            batch_op.drop_column("usage_sections")
