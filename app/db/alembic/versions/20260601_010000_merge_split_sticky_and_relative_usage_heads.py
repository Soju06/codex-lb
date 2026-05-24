"""Merge split sticky thresholds with relative availability and usage heads.

Revision ID: 20260601_010000_merge_split_sticky_and_relative_usage_heads
Revises: 20260515_020000_add_split_sticky_budget_thresholds,
    20260601_000000_merge_relative_availability_and_usage_raw_heads
Create Date: 2026-06-01 19:10:00.000000
"""

from __future__ import annotations

revision = "20260601_010000_merge_split_sticky_and_relative_usage_heads"
down_revision = (
    "20260515_020000_add_split_sticky_budget_thresholds",
    "20260601_000000_merge_relative_availability_and_usage_raw_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
