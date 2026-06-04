"""merge API key usage and reauth account status heads

Revision ID: 20260604_010000_merge_api_key_usage_and_reauth_heads
Revises: 20260603_010000_merge_api_key_usage_and_quota_visibility_heads,
    20260604_000000_add_reauth_required_account_status
Create Date: 2026-06-04 01:00:00.000000
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260604_010000_merge_api_key_usage_and_reauth_heads"
down_revision = (
    "20260603_010000_merge_api_key_usage_and_quota_visibility_heads",
    "20260604_000000_add_reauth_required_account_status",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
