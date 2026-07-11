"""merge automations and dashboard guest heads

Revision ID: 20260615_000000_merge_automations_and_dashboard_guest_heads
Revises:
- 20260611_000000_merge_automations_and_weekly_monthly_useragent_heads
- 20260611_000000_merge_dashboard_guest_and_weekly_useragent_heads
Create Date: 2026-06-15 00:00:00.000000
"""

from __future__ import annotations

revision = "20260615_000000_merge_automations_and_dashboard_guest_heads"
down_revision = (
    "20260611_000000_merge_automations_and_weekly_monthly_useragent_heads",
    "20260611_000000_merge_dashboard_guest_and_weekly_useragent_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
