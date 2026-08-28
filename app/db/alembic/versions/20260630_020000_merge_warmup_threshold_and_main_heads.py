"""merge warmup threshold and main migration heads

Revision ID: 20260630_020000_merge_warmup_threshold_and_main_heads
Revises:
- 20260623_000000_add_limit_warmup_exhausted_threshold
- 20260630_010000_merge_warmup_and_request_log_dashboard_heads
Create Date: 2026-06-30 02:00:00.000000
"""

from __future__ import annotations

revision = "20260630_020000_merge_warmup_threshold_and_main_heads"
down_revision = (
    "20260623_000000_add_limit_warmup_exhausted_threshold",
    "20260630_010000_merge_warmup_and_request_log_dashboard_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
