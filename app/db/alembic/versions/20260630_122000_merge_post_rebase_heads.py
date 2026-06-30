"""merge post-rebase migration heads

Revision ID: 20260630_122000_merge_post_rebase_heads
Revises:
- 20260629_010000_merge_dashboard_indexes_and_request_transport_heads
- 20260630_010000_merge_warmup_and_request_log_dashboard_heads
Create Date: 2026-06-30 12:20:00.000000
"""

from __future__ import annotations

revision = "20260630_122000_merge_post_rebase_heads"
down_revision = (
    "20260629_010000_merge_dashboard_indexes_and_request_transport_heads",
    "20260630_010000_merge_warmup_and_request_log_dashboard_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
