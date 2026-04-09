"""merge durable http bridge and import_without_overwrite heads

Revision ID: 20260409_010000_merge_http_bridge_and_import_without_overwrite_heads
Revises: 20260408_010000_merge_import_without_overwrite_and_assignment_heads,
20260409_000000_add_http_bridge_sessions
Create Date: 2026-04-09
"""

from __future__ import annotations

revision = "20260409_010000_merge_http_bridge_and_import_without_overwrite_heads"
down_revision = (
    "20260408_010000_merge_import_without_overwrite_and_assignment_heads",
    "20260409_000000_add_http_bridge_sessions",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    return None


def downgrade() -> None:
    return None
