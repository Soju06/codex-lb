"""merge routing policy and split sticky threshold heads

Revision ID: 20260515_025000_merge_routing_policy_and_split_sticky_heads
Revises: 20260515_010000_merge_routing_policy_and_request_log_heads, 20260515_020000_add_split_sticky_budget_thresholds
Create Date: 2026-05-15
"""

from __future__ import annotations

revision = "20260515_025000_merge_routing_policy_and_split_sticky_heads"
down_revision = (
    "20260515_010000_merge_routing_policy_and_request_log_heads",
    "20260515_020000_add_split_sticky_budget_thresholds",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
