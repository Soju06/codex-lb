"""merge routing policy and traffic class heads

Revision ID: 20260510_010000_merge_routing_policy_and_traffic_heads
Revises:
    20260509_010000_add_additional_quota_routing_policies
    20260509_020000_add_split_sticky_budget_thresholds
    20260514_000000_add_request_logs_api_key_time_index
Create Date: 2026-05-10
"""

from __future__ import annotations

revision = "20260510_010000_merge_routing_policy_and_traffic_heads"
down_revision = (
    "20260509_010000_add_additional_quota_routing_policies",
    "20260509_020000_add_split_sticky_budget_thresholds",
    "20260514_000000_add_request_logs_api_key_time_index",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
