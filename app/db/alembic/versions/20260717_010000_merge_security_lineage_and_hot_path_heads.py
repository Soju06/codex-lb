"""Merge security-lineage and dashboard hot-path heads.

Revision ID: 20260717_010000_merge_security_lineage_and_hot_path_heads
Revises: 20260716_020000_merge_security_lineage_and_retention_heads, 20260717_000000_optimize_dashboard_hot_path_indexes
Create Date: 2026-07-17 01:00:00.000000
"""

from __future__ import annotations

revision = "20260717_010000_merge_security_lineage_and_hot_path_heads"
down_revision = (
    "20260716_020000_merge_security_lineage_and_retention_heads",
    "20260717_000000_optimize_dashboard_hot_path_indexes",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
