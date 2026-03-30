"""merge scheduler leader and cache locality heads

Revision ID: 20260330_010000_merge_scheduler_leader_and_cache_locality_heads
Revises: 20260328_140000_add_scheduler_leader_table, 20260330_000000_add_cache_locality_settings
Create Date: 2026-03-30
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260330_010000_merge_scheduler_leader_and_cache_locality_heads"
down_revision = (
    "20260328_140000_add_scheduler_leader_table",
    "20260330_000000_add_cache_locality_settings",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    return


def downgrade() -> None:
    return
