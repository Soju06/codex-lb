"""Join recovery and permanent report history.

Revision ID: 20260909_070000_merge_recovery_and_report_rollup
Revises: 20260909_060000_merge_recovery_and_routing_settings,
    20260909_060000_add_report_rollup
Create Date: 2026-09-09
"""

from __future__ import annotations

revision = "20260909_070000_merge_recovery_and_report_rollup"
down_revision = (
    "20260909_060000_merge_recovery_and_routing_settings",
    "20260909_060000_add_report_rollup",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Converge existing histories without changing schemas or rows."""


def downgrade() -> None:
    """Restore parent stamps without changing schemas or rows."""
