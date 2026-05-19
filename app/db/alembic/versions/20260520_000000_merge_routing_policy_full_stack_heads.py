"""merge routing policy with current stack heads

Revision ID: 20260520_000000_merge_routing_policy_full_stack_heads
Revises: 20260518_010000_merge_routing_policy_and_http_bridge_heads,
    20260518_010000_merge_sticky_thresholds_and_http_bridge_heads,
    20260520_000000_merge_api_key_and_http_bridge_heads
Create Date: 2026-05-20
"""

from __future__ import annotations

revision = "20260520_000000_merge_routing_policy_full_stack_heads"
down_revision = (
    "20260518_010000_merge_routing_policy_and_http_bridge_heads",
    "20260518_010000_merge_sticky_thresholds_and_http_bridge_heads",
    "20260520_000000_merge_api_key_and_http_bridge_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
