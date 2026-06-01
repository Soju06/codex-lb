"""merge rebased usage migration heads

Revision ID: 20260601_010000_merge_rebased_usage_heads
Revises: 20260527_161500_merge_usage_window_and_account_heads,
    20260601_000000_merge_relative_availability_and_usage_raw_heads
Create Date: 2026-06-01
"""

from __future__ import annotations

revision = "20260601_010000_merge_rebased_usage_heads"
down_revision = (
    "20260527_161500_merge_usage_window_and_account_heads",
    "20260601_000000_merge_relative_availability_and_usage_raw_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
