"""Merge account usage limits with current upstream migrations.

Revision ID: 20261002_000000_merge_usage_limits_and_main_heads
Revises: 20260728_010000_add_account_usage_limits, 20260918_000000_merge_scim_and_overflow_heads
Create Date: 2026-10-02
"""

from __future__ import annotations

revision = "20261002_000000_merge_usage_limits_and_main_heads"
down_revision = (
    "20260728_010000_add_account_usage_limits",
    "20260918_000000_merge_scim_and_overflow_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
