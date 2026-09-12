"""Join recovery and dashboard routing settings histories.

Revision ID: 20260909_060000_merge_recovery_and_routing_settings
Revises: 20260909_050000_merge_recovery_and_dashboard_settings,
    20260909_050000_dashboard_routing_overload_settings
Create Date: 2026-09-09
"""

from __future__ import annotations

revision = "20260909_060000_merge_recovery_and_routing_settings"
down_revision = (
    "20260909_050000_merge_recovery_and_dashboard_settings",
    "20260909_050000_dashboard_routing_overload_settings",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Converge existing histories without changing schemas or rows."""


def downgrade() -> None:
    """Restore parent stamps without changing schemas or rows."""
