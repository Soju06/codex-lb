"""merge security work and request log index heads

Revision ID: 20260515_000000_merge_security_work_and_request_log_heads
Revises: 20260425_000000_add_account_security_work_authorized, 20260514_000000_add_request_logs_api_key_time_index
Create Date: 2026-05-15
"""

from __future__ import annotations

revision = "20260515_000000_merge_security_work_and_request_log_heads"
down_revision = (
    "20260425_000000_add_account_security_work_authorized",
    "20260514_000000_add_request_logs_api_key_time_index",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
