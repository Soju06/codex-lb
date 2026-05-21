"""merge request log account index and runtime API key heads

Revision ID: 20260521_000000_merge_request_log_account_index_and_runtime_api_key_heads
Revises: 20260520_010000_add_request_logs_api_key_account_index,
    20260520_010000_merge_runtime_and_api_key_bridge_heads
Create Date: 2026-05-21
"""

from __future__ import annotations

revision = "20260521_000000_merge_request_log_account_index_and_runtime_api_key_heads"
down_revision = (
    "20260520_010000_add_request_logs_api_key_account_index",
    "20260520_010000_merge_runtime_and_api_key_bridge_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
