"""merge request-log kind and account-status heads

Revision ID: 20260319_191500_merge_request_log_kind_and_account_status_heads
Revises: 20260311_000000_add_request_logs_kind_and_session_hash, 20260319_183000_normalize_sqlite_account_status_casing
Create Date: 2026-03-19
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260319_191500_merge_request_log_kind_and_account_status_heads"
down_revision = (
    "20260311_000000_add_request_logs_kind_and_session_hash",
    "20260319_183000_normalize_sqlite_account_status_casing",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
