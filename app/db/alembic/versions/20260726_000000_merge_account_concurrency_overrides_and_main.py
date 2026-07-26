"""merge account concurrency overrides and main heads

Revision ID: 20260726_000000_merge_account_concurrency_overrides_and_main
Revises:
- 20260718_000000_add_account_concurrency_overrides
- 20260724_000000_add_request_usage_time_rollups
Create Date: 2026-07-26 00:00:00.000000
"""

from __future__ import annotations

revision = "20260726_000000_merge_account_concurrency_overrides_and_main"
down_revision = (
    "20260718_000000_add_account_concurrency_overrides",
    "20260724_000000_add_request_usage_time_rollups",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
