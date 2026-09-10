"""Join recovery and pinned automation claim budgets.

Revision ID: 20260909_080000_merge_recovery_and_automation_budget
Revises: 20260909_070000_merge_recovery_and_report_rollup,
    20260909_070000_automation_run_claim_budget
Create Date: 2026-09-09
"""

from __future__ import annotations

revision = "20260909_080000_merge_recovery_and_automation_budget"
down_revision = (
    "20260909_070000_merge_recovery_and_report_rollup",
    "20260909_070000_automation_run_claim_budget",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Converge existing histories without changing schemas or rows."""


def downgrade() -> None:
    """Restore parent stamps without changing schemas or rows."""
