"""merge routing policy and request log heads

Revision ID: 20260515_010000_merge_routing_policy_and_request_log_heads
Revises: 20260509_010000_add_additional_quota_routing_policies, 20260514_000000_add_request_logs_api_key_time_index
Create Date: 2026-05-15
"""

from __future__ import annotations

revision = "20260515_010000_merge_routing_policy_and_request_log_heads"
down_revision = (
    "20260509_010000_add_additional_quota_routing_policies",
    "20260514_000000_add_request_logs_api_key_time_index",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
