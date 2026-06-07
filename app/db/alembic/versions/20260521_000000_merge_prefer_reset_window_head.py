"""merge prefer reset window and main migration heads

Revision ID: 20260521_000000_merge_prefer_reset_window_head
Revises: 20260515_000000_add_prefer_earlier_reset_window,
    20260520_010000_add_request_logs_api_key_account_index
Create Date: 2026-05-21
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260521_000000_merge_prefer_reset_window_head"
down_revision = (
    "20260515_000000_add_prefer_earlier_reset_window",
    "20260520_010000_add_request_logs_api_key_account_index",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
