"""merge usage raw window and account runtime heads

Revision ID: 20260527_161500_merge_usage_window_and_account_heads
Revises: 20260525_000000_add_usage_raw_window_latest_index,
    20260525_123500_merge_account_alias_and_runtime_api_key_heads
Create Date: 2026-05-27
"""

from __future__ import annotations

revision = "20260527_161500_merge_usage_window_and_account_heads"
down_revision = (
    "20260525_000000_add_usage_raw_window_latest_index",
    "20260525_123500_merge_account_alias_and_runtime_api_key_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
