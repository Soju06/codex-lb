"""merge cache invalidation and api key service tier heads

Revision ID: 20260401_020000_merge_cache_invalidation_and_api_key_service_tier_heads
Revises: 20260401_000000_add_cache_invalidation, 20260401_000000_add_api_key_enforced_service_tier
Create Date: 2026-04-01
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260401_020000_merge_cache_invalidation_and_api_key_service_tier_heads"
down_revision = ("20260401_000000_add_cache_invalidation", "20260401_000000_add_api_key_enforced_service_tier")
branch_labels = None
depends_on = None


def upgrade() -> None:
    return None


def downgrade() -> None:
    return None
