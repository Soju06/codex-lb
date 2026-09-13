"""Merge the subscription-overflow and transport-sentinel heads.

Revision ID: 20260908_020000_merge_overflow_transport_heads
Revises: 20260908_000000_add_subscription_overflow,
    20260908_000000_replace_upstream_stream_transport_default_sentinel
Create Date: 2026-09-08
"""

from __future__ import annotations

revision = "20260908_020000_merge_overflow_transport_heads"
down_revision = (
    "20260908_000000_add_subscription_overflow",
    "20260908_000000_replace_upstream_stream_transport_default_sentinel",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join revision tracking after both parents have completed."""
    pass


def downgrade() -> None:
    """Restore both parent stamps without changing their schema or data."""
    pass
