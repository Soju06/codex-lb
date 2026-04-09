"""fix durable http bridge last_seen index ordering

Revision ID: 20260409_020000_fix_http_bridge_last_seen_index
Revises: 20260409_010000_merge_http_bridge_and_import_without_overwrite_heads
Create Date: 2026-04-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260409_020000_fix_http_bridge_last_seen_index"
down_revision = "20260409_010000_merge_http_bridge_and_import_without_overwrite_heads"
branch_labels = None
depends_on = None


def _has_table(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "http_bridge_sessions"):
        return
    op.drop_index("idx_http_bridge_sessions_last_seen", table_name="http_bridge_sessions", if_exists=True)
    op.create_index(
        "idx_http_bridge_sessions_last_seen",
        "http_bridge_sessions",
        [sa.text("last_seen_at DESC")],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "http_bridge_sessions"):
        return
    op.drop_index("idx_http_bridge_sessions_last_seen", table_name="http_bridge_sessions", if_exists=True)
    op.create_index(
        "idx_http_bridge_sessions_last_seen",
        "http_bridge_sessions",
        ["last_seen_at"],
    )
