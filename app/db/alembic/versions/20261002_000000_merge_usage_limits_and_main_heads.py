"""Continue account usage-limit history after its reviewed override migration.

Revision ID: 20261002_000000_merge_usage_limits_and_main_heads
Revises: 20260910_010000_add_usage_limit_overrides
Create Date: 2026-10-02
"""

from __future__ import annotations

revision = "20261002_000000_merge_usage_limits_and_main_heads"
down_revision = "20260910_010000_add_usage_limit_overrides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
