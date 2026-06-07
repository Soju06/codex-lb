"""Merge relative availability and raw usage latest index heads.

Revision ID: 20260601_000000_merge_relative_availability_and_usage_raw_heads
Revises: 20260426_000000_add_dashboard_relative_availability_settings, 20260525_000000_add_usage_raw_window_latest_index
Create Date: 2026-06-01 03:10:00.000000
"""

from __future__ import annotations

revision = "20260601_000000_merge_relative_availability_and_usage_raw_heads"
down_revision = (
    "20260426_000000_add_dashboard_relative_availability_settings",
    "20260525_000000_add_usage_raw_window_latest_index",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
