"""merge warmup and request-log index heads

Revision ID: 20260521_000000_merge_warmup_and_request_log_index_heads
Revises: 20260427_130000_add_warmup_model_and_request_kind,
    20260520_010000_add_request_logs_api_key_account_index
Create Date: 2026-05-21
"""

from __future__ import annotations

revision = "20260521_000000_merge_warmup_and_request_log_index_heads"
down_revision = (
    "20260427_130000_add_warmup_model_and_request_kind",
    "20260513_000000_add_accounts_alias",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
