"""merge automation and request-log heads

Revision ID: 20260421_130000_merge_automation_and_request_log_heads
Revises: 20260419_020000_add_automation_run_cycles_snapshot_tables,
20260421_120000_merge_request_log_lookup_and_plan_type_heads
Create Date: 2026-04-21
"""

from __future__ import annotations

revision = "20260421_130000_merge_automation_and_request_log_heads"
down_revision = (
    "20260419_020000_add_automation_run_cycles_snapshot_tables",
    "20260421_120000_merge_request_log_lookup_and_plan_type_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    return


def downgrade() -> None:
    return
