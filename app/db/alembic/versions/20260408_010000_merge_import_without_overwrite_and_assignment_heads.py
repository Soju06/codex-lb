"""merge import without overwrite and assignment heads

Revision ID: 20260408_010000_merge_import_without_overwrite_and_assignment_heads
Revises: 20260407_010000_merge_api_key_assignment_and_bridge_gateway_heads,
20260408_000000_switch_import_without_overwrite_default_to_true
Create Date: 2026-04-08
"""

from __future__ import annotations

revision = "20260408_010000_merge_import_without_overwrite_and_assignment_heads"
down_revision = (
    "20260407_010000_merge_api_key_assignment_and_bridge_gateway_heads",
    "20260408_000000_switch_import_without_overwrite_default_to_true",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    return None


def downgrade() -> None:
    return None
