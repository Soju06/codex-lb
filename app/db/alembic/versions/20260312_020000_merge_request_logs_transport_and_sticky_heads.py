"""merge request_logs transport and sticky-session heads

Revision ID: 20260312_020000_merge_request_logs_transport_and_sticky_heads
Revises: 20260310_000000_add_request_logs_transport, 20260312_010000_merge_additional_usage_and_sticky_session_heads
Create Date: 2026-03-12 02:00:00.000000
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260312_020000_merge_request_logs_transport_and_sticky_heads"
down_revision = (
    "20260310_000000_add_request_logs_transport",
    "20260312_010000_merge_additional_usage_and_sticky_session_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
