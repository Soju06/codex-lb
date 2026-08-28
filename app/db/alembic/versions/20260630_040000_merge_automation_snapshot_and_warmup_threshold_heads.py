"""merge automation snapshot and warmup threshold heads

Revision ID: 20260630_040000_merge_automation_snapshot_and_warmup_threshold_heads
Revises:
- 20260630_030000_add_automation_run_model_snapshot
- 20260630_020000_merge_warmup_threshold_and_main_heads
Create Date: 2026-06-30 04:00:00.000000
"""

from __future__ import annotations

revision = "20260630_040000_merge_automation_snapshot_and_warmup_threshold_heads"
down_revision = (
    "20260630_030000_add_automation_run_model_snapshot",
    "20260630_020000_merge_warmup_threshold_and_main_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
