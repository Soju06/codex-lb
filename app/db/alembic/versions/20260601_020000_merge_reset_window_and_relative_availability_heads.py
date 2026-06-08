"""Merge reset-window routing and relative availability heads.

Revision ID: 20260601_020000_merge_reset_window_and_relative_availability_heads
Revises: 20260524_000000_merge_accounts_alias_and_reset_window_heads,
    20260601_000000_merge_relative_availability_and_usage_raw_heads
Create Date: 2026-06-01
"""

from __future__ import annotations

revision = "20260601_020000_merge_reset_window_and_relative_availability_heads"
down_revision = (
    "20260524_000000_merge_accounts_alias_and_reset_window_heads",
    "20260601_000000_merge_relative_availability_and_usage_raw_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
