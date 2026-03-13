"""merge additional usage and sticky session heads

Revision ID: 20260312_010000_merge_additional_usage_and_sticky_session_heads
Revises: 20260312_000000_add_additional_usage_quota_key, 20260312_000000_split_sticky_sessions_primary_key_by_kind
Create Date: 2026-03-12
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260312_010000_merge_additional_usage_and_sticky_session_heads"
down_revision = (
    "20260312_000000_add_additional_usage_quota_key",
    "20260312_000000_split_sticky_sessions_primary_key_by_kind",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    return


def downgrade() -> None:
    return
