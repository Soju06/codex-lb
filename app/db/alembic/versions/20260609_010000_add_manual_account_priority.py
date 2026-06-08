"""add manual account priority setting

Revision ID: 20260609_010000_add_manual_account_priority
Revises: 20260609_000000_add_agent_provider_accounts
Create Date: 2026-06-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260609_010000_add_manual_account_priority"
down_revision = "20260609_000000_add_agent_provider_accounts"
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
    if dashboard_columns and "manual_account_priority_ids_json" not in dashboard_columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "manual_account_priority_ids_json",
                    sa.Text(),
                    server_default=sa.text("'[]'"),
                    nullable=False,
                )
            )
    provider_columns = _columns(bind, "agent_provider_routing_settings")
    if provider_columns and "ordered_account_ids_json" not in provider_columns:
        with op.batch_alter_table("agent_provider_routing_settings") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "ordered_account_ids_json",
                    sa.Text(),
                    server_default=sa.text("'[]'"),
                    nullable=False,
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if "manual_account_priority_ids_json" not in columns:
        pass
    else:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.drop_column("manual_account_priority_ids_json")
    if "ordered_account_ids_json" in _columns(bind, "agent_provider_routing_settings"):
        with op.batch_alter_table("agent_provider_routing_settings") as batch_op:
            batch_op.drop_column("ordered_account_ids_json")
