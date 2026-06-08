"""merge routing policy and split sticky threshold heads

Revision ID: 20260601_020000_merge_routing_policy_and_split_threshold_heads
Revises: 20260601_010000_add_routing_policy_persistence, 20260601_010000_merge_split_sticky_and_relative_usage_heads
Create Date: 2026-06-01 20:00:00.000000
"""

from __future__ import annotations

revision = "20260601_020000_merge_routing_policy_and_split_threshold_heads"
down_revision = (
    "20260601_010000_add_routing_policy_persistence",
    "20260601_010000_merge_split_sticky_and_relative_usage_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
