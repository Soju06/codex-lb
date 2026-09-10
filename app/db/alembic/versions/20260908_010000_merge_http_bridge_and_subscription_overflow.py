"""Merge HTTP bridge recovery and subscription overflow schema histories.

Revision ID: 20260908_010000_merge_http_bridge_and_subscription_overflow
Revises: 20260906_000000_add_http_bridge_rebind_claim,
    20260908_000000_add_subscription_overflow
Create Date: 2026-09-08
"""

from __future__ import annotations

revision = "20260908_010000_merge_http_bridge_and_subscription_overflow"
down_revision = (
    "20260906_000000_add_http_bridge_rebind_claim",
    "20260908_000000_add_subscription_overflow",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join the existing schema branches without changing application data."""


def downgrade() -> None:
    """Restore both branch stamps without changing their schemas."""
