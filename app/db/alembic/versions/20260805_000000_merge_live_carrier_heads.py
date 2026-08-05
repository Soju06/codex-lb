"""Recognize deployed live-stack migration merge revision.

Revision ID: 20260805_000000_merge_live_carrier_heads
Revises: 20260802_000000_merge_bridge_and_capability_lineage_heads, 20260803_000000_merge_http_bridge_recovery_and_capability_lineage_heads
Create Date: 2026-08-05
"""

from __future__ import annotations

revision = "20260805_000000_merge_live_carrier_heads"
down_revision = (
    "20260802_000000_merge_bridge_and_capability_lineage_heads",
    "20260803_000000_merge_http_bridge_recovery_and_capability_lineage_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
