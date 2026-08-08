"""Merge claim-session changes with the current main migration head.

Revision ID: 20260807_010000_merge_claim_with_current_main_head
Revises: 20260806_120000_add_http_bridge_owner_process_epoch, 20260806_020000_add_usage_history_bulk_covering_indexes
Create Date: 2026-08-07
"""

revision = "20260807_010000_merge_claim_with_current_main_head"
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
