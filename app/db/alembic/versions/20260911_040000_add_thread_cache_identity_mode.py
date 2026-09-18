"""Add the thread cache identity mode setting and its per-API-key override.

Revision ID: 20260911_010000_add_thread_cache_identity_mode
Revises: 20260911_010000_merge_pin_index_and_affinity_heads
Create Date: 2026-09-11 01:00:00.000000

Both columns are nullable and are created without a server default, so an
upgrade introduces no decision: NULL on ``dashboard_settings`` inherits the
environment value and then the ``shared`` code default, and NULL on
``api_keys`` means "follow the fleet". ``shared`` reproduces today's outbound
bytes exactly, so an upgraded install behaves identically until an operator
sets a value.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260911_040000_add_thread_cache_identity_mode"
down_revision = "20260911_030000_add_local_login_policy"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("dashboard_settings", "thread_cache_identity_mode"):
        op.add_column(
            "dashboard_settings",
            sa.Column("thread_cache_identity_mode", sa.String(), nullable=True),
        )
    if not _has_column("api_keys", "thread_cache_identity_override"):
        op.add_column(
            "api_keys",
            sa.Column("thread_cache_identity_override", sa.String(), nullable=True),
        )


def downgrade() -> None:
    if _has_column("api_keys", "thread_cache_identity_override"):
        op.drop_column("api_keys", "thread_cache_identity_override")
    if _has_column("dashboard_settings", "thread_cache_identity_mode"):
        op.drop_column("dashboard_settings", "thread_cache_identity_mode")
