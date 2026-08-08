"""Merge bridge owner epoch migration with the current main head.

Revision ID: 20260807_010000_merge_owner_epoch_with_usage_indexes
Revises: 20260806_120000_add_http_bridge_owner_process_epoch, 20260806_020000_add_usage_history_bulk_covering_indexes
Create Date: 2026-08-07
"""

revision = "20260807_010000_merge_owner_epoch_with_usage_indexes"
down_revision = (
    "20260806_120000_add_http_bridge_owner_process_epoch",
    "20260806_020000_add_usage_history_bulk_covering_indexes",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
