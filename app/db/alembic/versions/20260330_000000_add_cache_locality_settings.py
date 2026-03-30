"""add cache-locality settings fields to dashboard_settings

Revision ID: 20260330_000000_add_cache_locality_settings
Revises: 20260325_000000_add_request_log_cost
Create Date: 2026-03-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260330_000000_add_cache_locality_settings"
down_revision = "20260325_000000_add_request_log_cost"
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
    if not columns:
        return

    if "http_responses_session_bridge_prompt_cache_idle_ttl_seconds" not in columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "http_responses_session_bridge_prompt_cache_idle_ttl_seconds",
                    sa.Integer(),
                    nullable=False,
                    server_default="3600",
                )
            )

    if "sticky_reallocation_budget_threshold_pct" not in columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "sticky_reallocation_budget_threshold_pct",
                    sa.Float(),
                    nullable=False,
                    server_default="95.0",
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if not columns:
        return

    if "http_responses_session_bridge_prompt_cache_idle_ttl_seconds" in columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.drop_column("http_responses_session_bridge_prompt_cache_idle_ttl_seconds")

    if "sticky_reallocation_budget_threshold_pct" in columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.drop_column("sticky_reallocation_budget_threshold_pct")
