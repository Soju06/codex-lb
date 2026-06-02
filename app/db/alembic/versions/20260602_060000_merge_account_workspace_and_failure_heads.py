"""merge account workspace and request log failure heads

Revision ID: 20260602_060000_merge_account_workspace_and_failure_heads
Revises: 20260531_000000_add_account_workspace_identity, 20260601_020000_merge_warmup_and_request_log_failure_heads
Create Date: 2026-06-02 06:00:00.000000
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260602_060000_merge_account_workspace_and_failure_heads"
down_revision = (
    "20260531_000000_add_account_workspace_identity",
    "20260601_020000_merge_warmup_and_request_log_failure_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
