"""Merge traffic-class and request-log summary heads.

Revision ID: 20260510_010000_merge_traffic_class_and_slim_summary_heads
Revises: 20260509_020000_add_split_sticky_budget_thresholds
         20260510_000000_add_request_log_slim_summary
Create Date: 2026-05-10 01:00:00.000000
"""

revision = "20260510_010000_merge_traffic_class_and_slim_summary_heads"
down_revision = (
    "20260509_020000_add_split_sticky_budget_thresholds",
    "20260510_000000_add_request_log_slim_summary",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
