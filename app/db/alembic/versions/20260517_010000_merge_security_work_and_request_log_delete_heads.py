"""merge security work and request log delete heads

Revision ID:
20260517_010000_merge_security_work_and_request_log_delete_heads
Revises:
20260515_000000_merge_security_work_and_request_log_heads,
20260515_000000_soft_delete_request_logs_on_account_delete
Create Date: 2026-05-17
"""

from __future__ import annotations

revision = "20260517_010000_merge_security_work_and_request_log_delete_heads"
down_revision = (
    "20260515_000000_merge_security_work_and_request_log_heads",
    "20260515_000000_soft_delete_request_logs_on_account_delete",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
