"""Merge single-account routing and routing quota heads.

Revision ID: 20260601_030000_merge_single_account_and_routing_quota_heads
Revises: 20260531_000000_add_single_account_routing,
    20260601_020000_merge_additional_quota_routing_and_relative_availability_heads,
    20260601_020000_merge_reset_window_and_relative_availability_heads,
    20260601_020000_merge_routing_policy_and_split_threshold_heads
Create Date: 2026-06-01
"""

from __future__ import annotations

revision = "20260601_030000_merge_single_account_and_routing_quota_heads"
down_revision = (
    "20260531_000000_add_single_account_routing",
    "20260601_020000_merge_additional_quota_routing_and_relative_availability_heads",
    "20260601_020000_merge_reset_window_and_relative_availability_heads",
    "20260601_020000_merge_routing_policy_and_split_threshold_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
