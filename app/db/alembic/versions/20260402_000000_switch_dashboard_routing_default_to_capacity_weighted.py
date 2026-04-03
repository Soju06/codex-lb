from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260402_000000_switch_dashboard_routing_default_to_capacity_weighted"
down_revision = "20260401_020000_merge_cache_invalidation_and_api_key_service_tier_heads"
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
    if not _table_exists(bind, "dashboard_settings"):
        return
    columns = _columns(bind, "dashboard_settings")
    if "routing_strategy" not in columns:
        return

    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.alter_column(
            "routing_strategy",
            existing_type=sa.String(),
            server_default=sa.text("'capacity_weighted'"),
        )

    if {"created_at", "updated_at"}.issubset(columns):
        op.execute(
            sa.text(
                """
                UPDATE dashboard_settings
                SET routing_strategy = 'capacity_weighted'
                WHERE routing_strategy = 'usage_weighted'
                  AND updated_at = created_at
                """
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "dashboard_settings"):
        return
    columns = _columns(bind, "dashboard_settings")
    if "routing_strategy" not in columns:
        return

    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.alter_column(
            "routing_strategy",
            existing_type=sa.String(),
            server_default=sa.text("'usage_weighted'"),
        )
