"""add staggered idle limit warm-up setting

Revision ID: 20260630_000000_add_limit_warmup_staggered_idle
Revises: 20260608_000000_add_hide_upstream_quota_from_api_keys
Create Date: 2026-06-30 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260630_000000_add_limit_warmup_staggered_idle"
down_revision = "20260608_000000_add_hide_upstream_quota_from_api_keys"
branch_labels = None
depends_on = None


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    dashboard_columns = _columns(bind, "dashboard_settings")
    if dashboard_columns and "limit_warmup_staggered_idle_enabled" not in dashboard_columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "limit_warmup_staggered_idle_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    dashboard_columns = _columns(bind, "dashboard_settings")
    if "limit_warmup_staggered_idle_enabled" in dashboard_columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.drop_column("limit_warmup_staggered_idle_enabled")
