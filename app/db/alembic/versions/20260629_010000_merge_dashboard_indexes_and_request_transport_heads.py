"""merge dashboard indexes and request transport heads

Revision ID: 20260629_010000_merge_dashboard_indexes_and_request_transport_heads
Revises:
- 20260626_020000_merge_rebased_and_request_log_transport_heads
- 20260629_000000_add_dashboard_query_hot_path_indexes
Create Date: 2026-06-29 01:00:00.000000
"""

from __future__ import annotations

revision = "20260629_010000_merge_dashboard_indexes_and_request_transport_heads"
down_revision = (
    "20260626_020000_merge_rebased_and_request_log_transport_heads",
    "20260629_000000_add_dashboard_query_hot_path_indexes",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
