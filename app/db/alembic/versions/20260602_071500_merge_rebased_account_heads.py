"""merge rebased usage and account workspace heads

Revision ID: 20260602_071500_merge_rebased_account_heads
Revises: 20260601_010000_merge_rebased_usage_heads,
    20260602_060000_merge_account_workspace_and_failure_heads
Create Date: 2026-06-02 07:15:00.000000
"""

from __future__ import annotations

revision = "20260602_071500_merge_rebased_account_heads"
down_revision = (
    "20260601_010000_merge_rebased_usage_heads",
    "20260602_060000_merge_account_workspace_and_failure_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
