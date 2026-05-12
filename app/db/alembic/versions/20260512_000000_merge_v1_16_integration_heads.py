"""merge v1.16 integration heads

Revision ID: 20260512_000000_merge_v1_16_integration_heads
Revises: 20260424_000000_merge_dashboard_session_ttl_and_request_log_heads,
20260424_000000_merge_platform_identity_and_request_log_heads
Create Date: 2026-05-12
"""

from __future__ import annotations

revision = "20260512_000000_merge_v1_16_integration_heads"
down_revision = (
    "20260424_000000_merge_dashboard_session_ttl_and_request_log_heads",
    "20260424_000000_merge_platform_identity_and_request_log_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    return


def downgrade() -> None:
    return
