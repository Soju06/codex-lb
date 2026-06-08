"""merge warmup and request log failure metadata heads

Revision ID: 20260601_020000_merge_warmup_and_request_log_failure_heads
Revises: 20260526_000000_add_request_log_failure_metadata,
    20260601_010000_merge_warmup_and_relative_availability_heads
Create Date: 2026-06-01
"""

from __future__ import annotations

revision = "20260601_020000_merge_warmup_and_request_log_failure_heads"
down_revision = (
    "20260526_000000_add_request_log_failure_metadata",
    "20260601_010000_merge_warmup_and_relative_availability_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
