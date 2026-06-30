"""merge dashboard guest and request-log client-ip heads

Revision ID: 20260611_030000_merge_dashboard_guest_and_request_log_client_ip_heads
Revises:
- 20260611_000000_merge_dashboard_guest_and_weekly_useragent_heads
- 20260611_020000_merge_request_log_archive_and_client_ip_heads
Create Date: 2026-06-11 03:00:00.000000
"""

from __future__ import annotations

revision = "20260611_030000_merge_dashboard_guest_and_request_log_client_ip_heads"
down_revision = (
    "20260611_000000_merge_dashboard_guest_and_weekly_useragent_heads",
    "20260611_020000_merge_request_log_archive_and_client_ip_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
