"""merge retention and reset credit display heads

Revision ID: 20260717_000000_merge_retention_and_reset_credit_display_heads
Revises:
- 20260716_010000_add_dashboard_retention_settings
- 20260716_010000_add_reset_credit_display_settings
Create Date: 2026-07-17 00:00:00.000000
"""

from __future__ import annotations

revision = "20260717_000000_merge_retention_and_reset_credit_display_heads"
down_revision = (
    "20260716_010000_add_dashboard_retention_settings",
    "20260716_010000_add_reset_credit_display_settings",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
