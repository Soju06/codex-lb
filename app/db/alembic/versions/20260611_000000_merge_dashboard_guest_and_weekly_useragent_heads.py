"""merge dashboard guest and weekly/monthly/useragent heads

Revision ID: 20260611_000000_merge_dashboard_guest_and_weekly_useragent_heads
Revises:
- 20260604_010000_merge_dashboard_guest_and_reauth_status_heads
- 20260607_000000_merge_weekly_monthly_useragent_heads
Create Date: 2026-06-11 00:00:00.000000
"""

from __future__ import annotations

revision = "20260611_000000_merge_dashboard_guest_and_weekly_useragent_heads"
down_revision = (
    "20260604_010000_merge_dashboard_guest_and_reauth_status_heads",
    "20260607_000000_merge_weekly_monthly_useragent_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
