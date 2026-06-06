"""merge rebased routing and reauth-required heads

Revision ID: 20260606_000000_merge_rebased_reauth_required_heads
Revises: 20260603_124500_merge_rebased_proxy_routing_heads,
    20260604_000000_add_reauth_required_account_status
Create Date: 2026-06-06
"""

from __future__ import annotations

revision = "20260606_000000_merge_rebased_reauth_required_heads"
down_revision = (
    "20260603_124500_merge_rebased_proxy_routing_heads",
    "20260604_000000_add_reauth_required_account_status",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
