"""Join HTTP bridge recovery and dashboard timeout settings histories.

Revision ID: 20260909_050000_merge_recovery_and_dashboard_settings
Revises: 20260909_000000_merge_recovery_and_upstream_heads,
    20260909_040000_dashboard_timeout_settings
Create Date: 2026-09-09
"""

from __future__ import annotations

revision = "20260909_050000_merge_recovery_and_dashboard_settings"
down_revision = (
    "20260909_000000_merge_recovery_and_upstream_heads",
    "20260909_040000_dashboard_timeout_settings",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Converge existing histories without changing schemas or rows."""


def downgrade() -> None:
    """Restore parent stamps without changing schemas or rows."""
