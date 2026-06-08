"""merge weekly pace, monthly quota, and useragent heads

Revision ID: 20260607_000000_merge_weekly_monthly_useragent_heads
Revises:
- 20260603_000000_add_weekly_pace_working_days
- 20260603_000000_free_account_monthly_window
- 20260604_010000_merge_useragent_and_reauth_heads
Create Date: 2026-06-07 00:00:00.000000
"""

from __future__ import annotations

revision = "20260607_000000_merge_weekly_monthly_useragent_heads"
down_revision = (
    "20260603_000000_add_weekly_pace_working_days",
    "20260603_000000_free_account_monthly_window",
    "20260604_010000_merge_useragent_and_reauth_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
