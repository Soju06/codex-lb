"""merge prompt-cache ttl and upstream transport heads

Revision ID: 20260319_111500_merge_prompt_cache_ttl_and_upstream_transport_heads
Revises: 20260312_120000_add_dashboard_upstream_stream_transport, 20260319_100937_increase_prompt_cache_ttl_to_1800s
Create Date: 2026-03-19
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260319_111500_merge_prompt_cache_ttl_and_upstream_transport_heads"
down_revision = (
    "20260312_120000_add_dashboard_upstream_stream_transport",
    "20260319_100937_increase_prompt_cache_ttl_to_1800s",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
