"""Merge upstream proxy and quota planner heads.

Revision ID: 20260602_080000_merge_upstream_proxy_and_quota_planner_heads
Revises: 20260602_050000_add_upstream_proxy_routing,
    20260602_070000_merge_quota_planner_and_account_workspace_heads
Create Date: 2026-06-02
"""

from __future__ import annotations

revision = "20260602_080000_merge_upstream_proxy_and_quota_planner_heads"
down_revision = (
    "20260602_050000_add_upstream_proxy_routing",
    "20260602_070000_merge_quota_planner_and_account_workspace_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
