"""add subagent prompt-cache TTL and sticky is_subagent flag

Revision ID: 20260713_080000_add_subagent_prompt_cache_ttl
Revises: 20260717_000000_optimize_dashboard_hot_path_indexes
Create Date: 2026-07-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260713_080000_add_subagent_prompt_cache_ttl"
down_revision = "20260717_000000_optimize_dashboard_hot_path_indexes"
branch_labels = None
depends_on = None


def _columns(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()

    settings_columns = _columns(bind, "dashboard_settings")
    if settings_columns and "http_responses_session_bridge_subagent_prompt_cache_ttl_seconds" not in settings_columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "http_responses_session_bridge_subagent_prompt_cache_ttl_seconds",
                    sa.Integer(),
                    nullable=True,
                    server_default=None,
                )
            )

    sticky_columns = _columns(bind, "sticky_sessions")
    if sticky_columns and "is_subagent" not in sticky_columns:
        with op.batch_alter_table("sticky_sessions") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "is_subagent",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )


def downgrade() -> None:
    bind = op.get_bind()

    settings_columns = _columns(bind, "dashboard_settings")
    if settings_columns and "http_responses_session_bridge_subagent_prompt_cache_ttl_seconds" in settings_columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.drop_column("http_responses_session_bridge_subagent_prompt_cache_ttl_seconds")

    sticky_columns = _columns(bind, "sticky_sessions")
    if sticky_columns and "is_subagent" in sticky_columns:
        with op.batch_alter_table("sticky_sessions") as batch_op:
            batch_op.drop_column("is_subagent")
