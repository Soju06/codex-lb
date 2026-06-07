"""merge useragent and reauth status heads

Revision ID: 20260604_010000_merge_useragent_and_reauth_heads
Revises: 20260602_080000_merge_useragent_and_upstream_proxy_heads, 20260604_000000_add_reauth_required_account_status
Create Date: 2026-06-04 01:00:00.000000
"""

from __future__ import annotations

revision = "20260604_010000_merge_useragent_and_reauth_heads"
down_revision = (
    "20260602_080000_merge_useragent_and_upstream_proxy_heads",
    "20260604_000000_add_reauth_required_account_status",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
