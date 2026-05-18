"""merge sticky thresholds with current main head

Revision ID: 20260518_010000_merge_sticky_thresholds_and_http_bridge_heads
Revises: 20260515_020000_add_split_sticky_budget_thresholds,
    20260520_010000_add_request_logs_api_key_account_index
Create Date: 2026-05-18
"""

from __future__ import annotations

revision = "20260518_010000_merge_sticky_thresholds_and_http_bridge_heads"
down_revision = (
    "20260515_020000_add_split_sticky_budget_thresholds",
    "20260520_010000_add_request_logs_api_key_account_index",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
