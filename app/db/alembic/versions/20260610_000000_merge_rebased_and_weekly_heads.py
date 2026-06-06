"""merge rebased and weekly/monthly/useragent heads

Revision ID: 20260610_000000_merge_rebased_and_weekly_heads
Revises:
- 20260606_000000_merge_rebased_reauth_required_heads
- 20260607_000000_merge_weekly_monthly_useragent_heads
Create Date: 2026-06-10 00:00:00.000000
"""

from __future__ import annotations

revision = "20260610_000000_merge_rebased_and_weekly_heads"
down_revision = (
    "20260606_000000_merge_rebased_reauth_required_heads",
    "20260607_000000_merge_weekly_monthly_useragent_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
