"""merge quota planner and request log index heads

Revision ID: 20260520_040000_merge_quota_planner_and_request_log_index_heads
Revises: 20260513_000000_add_accounts_alias, 20260520_030000_add_quota_planner
Create Date: 2026-05-20
"""

from __future__ import annotations

revision = "20260520_040000_merge_quota_planner_and_request_log_index_heads"
down_revision = (
    "20260513_000000_add_accounts_alias",
    "20260520_030000_add_quota_planner",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
