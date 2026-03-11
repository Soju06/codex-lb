"""add sticky session kinds and prompt-cache affinity ttl

Revision ID: 20260310_120000_add_sticky_session_kinds_and_affinity_ttl
Revises: 20260310_000000_fix_postgresql_enum_value_casing
Create Date: 2026-03-10
"""

from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260310_120000_add_sticky_session_kinds_and_affinity_ttl"
down_revision = "20260310_000000_fix_postgresql_enum_value_casing"
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


def _indexes(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(index["name"]) for index in inspector.get_indexes(table_name) if index.get("name") is not None}


def _prompt_cache_ttl_default() -> int:
    raw = os.getenv("CODEX_LB_OPENAI_CACHE_AFFINITY_MAX_AGE_SECONDS", "300").strip()
    try:
        value = int(raw)
    except ValueError:
        return 300
    return value if value > 0 else 300


def _sticky_session_kind_enum() -> sa.Enum:
    return sa.Enum(
        "codex_session",
        "sticky_thread",
        "prompt_cache",
        name="sticky_session_kind",
    )


def upgrade() -> None:
    bind = op.get_bind()
    ttl_default = _prompt_cache_ttl_default()

    if _table_exists(bind, "sticky_sessions"):
        sticky_columns = _columns(bind, "sticky_sessions")
        if "kind" not in sticky_columns:
            if bind.dialect.name == "postgresql":
                _sticky_session_kind_enum().create(bind, checkfirst=True)
            with op.batch_alter_table("sticky_sessions") as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "kind",
                        _sticky_session_kind_enum(),
                        nullable=False,
                        server_default=sa.text("'sticky_thread'"),
                        )
                )
        if bind.dialect.name == "postgresql":
            op.execute(sa.text("UPDATE sticky_sessions SET kind = 'sticky_thread' WHERE kind IS NULL"))
        else:
            op.execute(sa.text("UPDATE sticky_sessions SET kind = 'sticky_thread' WHERE kind IS NULL OR kind = ''"))
        sticky_indexes = _indexes(bind, "sticky_sessions")
        if "idx_sticky_kind_updated_at" not in sticky_indexes:
            op.execute(
                sa.text(
                    "CREATE INDEX idx_sticky_kind_updated_at "
                    "ON sticky_sessions (kind, updated_at DESC)"
                )
            )

    if _table_exists(bind, "dashboard_settings"):
        dashboard_columns = _columns(bind, "dashboard_settings")
        if "openai_cache_affinity_max_age_seconds" not in dashboard_columns:
            with op.batch_alter_table("dashboard_settings") as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "openai_cache_affinity_max_age_seconds",
                        sa.Integer(),
                        nullable=False,
                        server_default=sa.text(str(ttl_default)),
                    )
                )
        bind.execute(
            sa.text(
                """
                UPDATE dashboard_settings
                SET openai_cache_affinity_max_age_seconds = :ttl_default
                WHERE openai_cache_affinity_max_age_seconds IS NULL
                """
            ),
            {"ttl_default": ttl_default},
        )


def downgrade() -> None:
    # Downgrade intentionally keeps additive columns to avoid SQLite table rebuild risk.
    return
