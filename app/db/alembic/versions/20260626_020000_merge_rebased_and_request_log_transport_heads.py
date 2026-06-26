"""merge rebased and request-log transport heads

Revision ID: 20260626_020000_merge_rebased_and_request_log_transport_heads
Revises:
- 20260615_000000_merge_rebased_and_dashboard_guest_heads
- 20260626_010000_add_request_logs_upstream_transport
Create Date: 2026-06-26 02:00:00.000000
"""

from __future__ import annotations

revision = "20260626_020000_merge_rebased_and_request_log_transport_heads"
down_revision = (
    "20260615_000000_merge_rebased_and_dashboard_guest_heads",
    "20260626_010000_add_request_logs_upstream_transport",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
