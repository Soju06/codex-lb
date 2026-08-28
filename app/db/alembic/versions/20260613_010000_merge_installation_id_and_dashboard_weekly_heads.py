"""merge installation id and dashboard/weekly/useragent heads

Revision ID: 20260613_010000_merge_installation_id_and_dashboard_weekly_heads
Revises:
- 20260611_000000_merge_dashboard_guest_and_weekly_useragent_heads
- 20260613_000000_add_accounts_codex_installation_id
Create Date: 2026-06-13 01:00:00.000000
"""

from __future__ import annotations

revision = "20260613_010000_merge_installation_id_and_dashboard_weekly_heads"
down_revision = (
    "20260611_000000_merge_dashboard_guest_and_weekly_useragent_heads",
    "20260613_000000_add_accounts_codex_installation_id",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
