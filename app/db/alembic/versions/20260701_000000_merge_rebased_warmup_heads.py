"""merge rebased warmup migration heads

Revision ID: 20260701_000000_merge_rebased_warmup_heads
Revises:
- 20260630_020000_merge_warmup_threshold_and_main_heads
- 20260630_122000_merge_post_rebase_heads
Create Date: 2026-07-01 00:00:00.000000
"""

from __future__ import annotations

revision = "20260701_000000_merge_rebased_warmup_heads"
down_revision = (
    "20260630_020000_merge_warmup_threshold_and_main_heads",
    "20260630_122000_merge_post_rebase_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
