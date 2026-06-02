"""Merge quota planner and account workspace heads.

Revision ID: 20260602_070000_merge_quota_planner_and_account_workspace_heads
Revises: 20260601_050000_merge_quota_planner_and_routing_consolidation_heads,
    20260602_060000_merge_account_workspace_and_failure_heads
Create Date: 2026-06-02
"""

from __future__ import annotations

revision = "20260602_070000_merge_quota_planner_and_account_workspace_heads"
down_revision = (
    "20260601_050000_merge_quota_planner_and_routing_consolidation_heads",
    "20260602_060000_merge_account_workspace_and_failure_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
