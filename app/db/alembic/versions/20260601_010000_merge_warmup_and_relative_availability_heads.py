"""merge warmup and relative availability heads

Revision ID: 20260601_010000_merge_warmup_and_relative_availability_heads
Revises: 20260521_000000_merge_warmup_and_request_log_index_heads,
    20260601_000000_merge_relative_availability_and_usage_raw_heads
Create Date: 2026-06-01
"""

from __future__ import annotations

revision = "20260601_010000_merge_warmup_and_relative_availability_heads"
down_revision = (
    "20260521_000000_merge_warmup_and_request_log_index_heads",
    "20260601_000000_merge_relative_availability_and_usage_raw_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
