"""merge platform fallback and import heads

Revision ID: 20260408_020000_merge_platform_fallback_and_import_heads
Revises: 20260407_030000_rekey_sticky_sessions_by_provider,
20260408_010000_merge_import_without_overwrite_and_assignment_heads
Create Date: 2026-04-08
"""

from __future__ import annotations

revision = "20260408_020000_merge_platform_fallback_and_import_heads"
down_revision = (
    "20260407_030000_rekey_sticky_sessions_by_provider",
    "20260408_010000_merge_import_without_overwrite_and_assignment_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    return None


def downgrade() -> None:
    return None
