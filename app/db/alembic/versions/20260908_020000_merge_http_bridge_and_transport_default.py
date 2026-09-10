"""Merge HTTP bridge recovery and upstream transport-default histories.

Revision ID: 20260908_020000_merge_http_bridge_and_transport_default
Revises: 20260908_010000_merge_http_bridge_and_subscription_overflow,
    20260908_000000_replace_upstream_stream_transport_default_sentinel
Create Date: 2026-09-08
"""

from __future__ import annotations

revision = "20260908_020000_merge_http_bridge_and_transport_default"
down_revision = (
    "20260908_010000_merge_http_bridge_and_subscription_overflow",
    "20260908_000000_replace_upstream_stream_transport_default_sentinel",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join existing migration histories without changing application data."""


def downgrade() -> None:
    """Restore both parent stamps without changing their schemas or data."""
