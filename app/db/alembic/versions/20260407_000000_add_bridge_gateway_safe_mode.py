"""add bridge gateway-safe mode to dashboard settings

Revision ID: 20260407_000000_add_bridge_gateway_safe_mode
Revises: 20260403_000000_add_credit_api_key_limit_values
Create Date: 2026-04-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260407_000000_add_bridge_gateway_safe_mode"
down_revision = "20260403_000000_add_credit_api_key_limit_values"
branch_labels = None
depends_on = None


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if not columns or "http_responses_session_bridge_gateway_safe_mode" in columns:
        return

    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.add_column(
            sa.Column(
                "http_responses_session_bridge_gateway_safe_mode",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if not columns or "http_responses_session_bridge_gateway_safe_mode" not in columns:
        return

    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.drop_column("http_responses_session_bridge_gateway_safe_mode")
