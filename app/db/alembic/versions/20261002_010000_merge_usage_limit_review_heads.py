"""Join published window overrides and the reviewed upstream merge.

Revision ID: 20261002_010000_merge_usage_limit_review_heads
Revises: 20260910_010000_add_usage_limit_overrides, 20261002_000000_merge_usage_limits_and_main_heads
Create Date: 2026-10-02
"""

revision = "20261002_010000_merge_usage_limit_review_heads"
down_revision = (
    "20260910_010000_add_usage_limit_overrides",
    "20261002_000000_merge_usage_limits_and_main_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
