"""merge account alias and runtime API key heads

Revision ID: 20260525_123500_merge_account_alias_and_runtime_api_key_heads
Revises: 20260513_000000_add_accounts_alias,
    20260521_000000_merge_request_log_account_index_and_runtime_api_key_heads
Create Date: 2026-05-25
"""

from __future__ import annotations

revision = "20260525_123500_merge_account_alias_and_runtime_api_key_heads"
down_revision = (
    "20260513_000000_add_accounts_alias",
    "20260521_000000_merge_request_log_account_index_and_runtime_api_key_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
