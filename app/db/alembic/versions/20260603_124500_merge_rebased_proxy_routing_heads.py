"""merge rebased proxy routing and account heads

Revision ID: 20260603_124500_merge_rebased_proxy_routing_heads
Revises: 20260602_050000_add_upstream_proxy_routing,
    20260602_071500_merge_rebased_account_heads
Create Date: 2026-06-03 12:45:00.000000
"""

from __future__ import annotations

revision = "20260603_124500_merge_rebased_proxy_routing_heads"
down_revision = (
    "20260602_050000_add_upstream_proxy_routing",
    "20260602_071500_merge_rebased_account_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
