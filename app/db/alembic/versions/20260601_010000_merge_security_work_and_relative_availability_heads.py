"""Merge security work and relative availability heads.

Revision ID: 20260601_010000_merge_security_work_and_relative_availability_heads
Revises: 20260521_000000_add_account_security_work_authorized,
    20260601_000000_merge_relative_availability_and_usage_raw_heads
Create Date: 2026-06-01 19:00:00.000000
"""

from __future__ import annotations

revision = "20260601_010000_merge_security_work_and_relative_availability_heads"
down_revision = (
    "20260521_000000_add_account_security_work_authorized",
    "20260601_000000_merge_relative_availability_and_usage_raw_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
