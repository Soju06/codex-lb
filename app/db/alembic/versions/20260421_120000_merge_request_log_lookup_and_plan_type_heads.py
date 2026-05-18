"""merge request log lookup and plan type heads

Revision ID: 20260421_120000_merge_request_log_lookup_and_plan_type_heads
Revises: 20260415_160000_add_request_logs_response_lookup_index,
20260417_000000_add_request_log_plan_type
Create Date: 2026-04-21
"""

from __future__ import annotations

revision = "20260421_120000_merge_request_log_lookup_and_plan_type_heads"
down_revision = (
    "20260415_160000_add_request_logs_response_lookup_index",
    "20260417_000000_add_request_log_plan_type",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    return


def downgrade() -> None:
    return
