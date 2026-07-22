"""Merge security-lineage and request-log conversation heads.

Revision ID: 20260722_000000_merge_security_lineage_and_conversation_heads
Revises: 20260717_010000_merge_security_lineage_and_hot_path_heads, 20260720_000000_add_request_log_conversation_id
Create Date: 2026-07-22 23:45:00.000000
"""

from __future__ import annotations

revision = "20260722_000000_merge_security_lineage_and_conversation_heads"
down_revision = (
    "20260717_010000_merge_security_lineage_and_hot_path_heads",
    "20260720_000000_add_request_log_conversation_id",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
