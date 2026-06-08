"""merge API key usage sections and quota visibility heads

Revision ID: 20260608_010000_merge_api_key_usage_and_quota_visibility_heads
Revises: 20260601_010000_add_api_key_usage_sections,
    20260608_000000_add_hide_upstream_quota_from_api_keys
Create Date: 2026-06-08 01:00:00.000000
"""

from __future__ import annotations

revision = "20260608_010000_merge_api_key_usage_and_quota_visibility_heads"
down_revision = (
    "20260601_010000_add_api_key_usage_sections",
    "20260608_000000_add_hide_upstream_quota_from_api_keys",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
