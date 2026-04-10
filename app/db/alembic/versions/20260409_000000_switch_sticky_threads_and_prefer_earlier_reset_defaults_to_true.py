"""switch sticky_threads_enabled and prefer_earlier_reset_accounts defaults to true

Revision ID: 20260409_000000_switch_sticky_threads_and_prefer_earlier_reset_defaults_to_true
Revises: 20260408_010000_merge_import_without_overwrite_and_assignment_heads
Create Date: 2026-04-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260409_000000_switch_sticky_threads_and_prefer_earlier_reset_defaults_to_true"
down_revision = "20260408_010000_merge_import_without_overwrite_and_assignment_heads"
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
    config = op.get_context().config
    fresh_install = bool(config.attributes.get("codex_lb_fresh_install")) if config is not None else False

    if "sticky_threads_enabled" in columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.alter_column(
                "sticky_threads_enabled",
                existing_type=sa.Boolean(),
                server_default=sa.true(),
            )

    if "prefer_earlier_reset_accounts" in columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.alter_column(
                "prefer_earlier_reset_accounts",
                existing_type=sa.Boolean(),
                server_default=sa.true(),
            )

    if fresh_install:
        op.execute(
            sa.text(
                """
                UPDATE dashboard_settings
                SET sticky_threads_enabled = TRUE,
                    prefer_earlier_reset_accounts = TRUE
                WHERE id = 1
                """
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "dashboard_settings"):
        return
    columns = _columns(bind, "dashboard_settings")

    if "sticky_threads_enabled" in columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.alter_column(
                "sticky_threads_enabled",
                existing_type=sa.Boolean(),
                server_default=sa.false(),
            )

    if "prefer_earlier_reset_accounts" in columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.alter_column(
                "prefer_earlier_reset_accounts",
                existing_type=sa.Boolean(),
                server_default=sa.false(),
            )
