"""merge dashboard guest access and reauth status heads

Revision ID: 20260604_010000_merge_dashboard_guest_and_reauth_status_heads
Revises: 20260521_000000_merge_dashboard_guest_and_api_key_heads,
    20260604_000000_add_reauth_required_account_status
Create Date: 2026-06-04
"""

from __future__ import annotations

revision = "20260604_010000_merge_dashboard_guest_and_reauth_status_heads"
down_revision = (
    "20260521_000000_merge_dashboard_guest_and_api_key_heads",
    "20260604_000000_add_reauth_required_account_status",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
