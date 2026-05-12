"""merge dashboard session ttl and request log heads

Revision ID: 20260424_000000_merge_dashboard_session_ttl_and_request_log_heads
Revises: 20260421_000000_add_dashboard_session_ttl_setting,
20260423_120000_add_api_key_limit_reset_at_index
Create Date: 2026-04-24
"""

from __future__ import annotations

revision = "20260424_000000_merge_dashboard_session_ttl_and_request_log_heads"
down_revision = (
    "20260421_000000_add_dashboard_session_ttl_setting",
    "20260423_120000_add_api_key_limit_reset_at_index",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    return


def downgrade() -> None:
    return
