"""merge current live stack migration heads

Revision ID: 20260517_000000_merge_live_stack_heads
Revises:
    20260515_050000_live_schema_compat
    20260516_000000_add_sqlite_hot_path_indexes
Create Date: 2026-05-17
"""

from __future__ import annotations

revision = "20260517_000000_merge_live_stack_heads"
down_revision = (
    "20260515_050000_live_schema_compat",
    "20260516_000000_add_sqlite_hot_path_indexes",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
