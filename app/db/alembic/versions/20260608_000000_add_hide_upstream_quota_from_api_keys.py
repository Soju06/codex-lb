"""add hide upstream quota from api keys setting

Revision ID: 20260608_000000_add_hide_upstream_quota_from_api_keys
Revises: 20260601_010000_add_api_key_usage_sections
Create Date: 2026-06-08 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260608_000000_add_hide_upstream_quota_from_api_keys"
down_revision = "20260601_010000_add_api_key_usage_sections"
branch_labels = None
depends_on = None


def _has_table(connection: Connection, table_name: str) -> bool:
    return sa.inspect(connection).has_table(table_name)


def _columns(connection: Connection, table_name: str) -> set[str]:
    if not _has_table(connection, table_name):
        return set()
    return {column["name"] for column in sa.inspect(connection).get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    dashboard_columns = _columns(bind, "dashboard_settings")
    if dashboard_columns and "hide_upstream_quota_from_api_keys" not in dashboard_columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "hide_upstream_quota_from_api_keys",
                    sa.Boolean(),
                    server_default=sa.false(),
                    nullable=False,
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    dashboard_columns = _columns(bind, "dashboard_settings")
    if dashboard_columns and "hide_upstream_quota_from_api_keys" in dashboard_columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.drop_column("hide_upstream_quota_from_api_keys")
