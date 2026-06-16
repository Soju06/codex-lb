"""merge API key quota visibility and dashboard guest heads

Revision ID: 20260616_000000_merge_api_key_quota_and_dashboard_guest_heads
Revises:
- 20260608_010000_merge_api_key_usage_and_quota_visibility_heads
- 20260611_000000_merge_dashboard_guest_and_weekly_useragent_heads
Create Date: 2026-06-16 00:00:00.000000
"""

from __future__ import annotations

revision = "20260616_000000_merge_api_key_quota_and_dashboard_guest_heads"
down_revision = (
    "20260608_010000_merge_api_key_usage_and_quota_visibility_heads",
    "20260611_000000_merge_dashboard_guest_and_weekly_useragent_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
