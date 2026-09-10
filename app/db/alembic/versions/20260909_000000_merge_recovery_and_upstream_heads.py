"""Join recovery and upstream overflow/transport merge histories.

Revision ID: 20260909_000000_merge_recovery_and_upstream_heads
Revises: 20260908_020000_merge_http_bridge_and_transport_default,
    20260908_020000_merge_overflow_transport_heads
Create Date: 2026-09-09
"""

from __future__ import annotations

revision = "20260909_000000_merge_recovery_and_upstream_heads"
down_revision = (
    "20260908_020000_merge_http_bridge_and_transport_default",
    "20260908_020000_merge_overflow_transport_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Converge existing histories without changing schemas or rows."""


def downgrade() -> None:
    """Restore parent stamps without changing schemas or rows."""
