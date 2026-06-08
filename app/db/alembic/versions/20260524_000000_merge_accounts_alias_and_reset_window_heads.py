"""merge accounts alias and reset window heads

Revision ID: 20260524_000000_merge_accounts_alias_and_reset_window_heads
Revises: 20260513_000000_add_accounts_alias, 20260521_000000_merge_prefer_reset_window_head
Create Date: 2026-05-24
"""

from __future__ import annotations

revision = "20260524_000000_merge_accounts_alias_and_reset_window_heads"
down_revision = (
    "20260513_000000_add_accounts_alias",
    "20260521_000000_merge_prefer_reset_window_head",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
