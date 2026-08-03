"""Merge live bridge and capability lineage migration heads.

Revision ID: 20260802_000000_merge_bridge_and_capability_lineage_heads
Revises: 20260729_000000_drop_legacy_bridge_pending_tool_columns, 20260731_000000_add_capability_lineage_markers
Create Date: 2026-08-02
"""

from __future__ import annotations

revision = "20260802_000000_merge_bridge_and_capability_lineage_heads"
down_revision = (
    "20260729_000000_drop_legacy_bridge_pending_tool_columns",
    "20260731_000000_add_capability_lineage_markers",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
