"""Merge security work and routing consolidation heads.

Revision ID: 20260601_040000_merge_security_work_and_routing_consolidation_heads
Revises: 20260601_010000_merge_security_work_and_relative_availability_heads,
    20260601_030000_merge_single_account_and_routing_quota_heads
Create Date: 2026-06-01
"""

from __future__ import annotations

revision = "20260601_040000_merge_security_work_and_routing_consolidation_heads"
down_revision = (
    "20260601_010000_merge_security_work_and_relative_availability_heads",
    "20260601_030000_merge_single_account_and_routing_quota_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
