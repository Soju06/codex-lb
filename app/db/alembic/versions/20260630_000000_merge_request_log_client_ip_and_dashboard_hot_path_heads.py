"""merge request-log client-ip and dashboard hot-path heads

Revision ID: 20260630_000000_merge_request_log_client_ip_and_dashboard_hot_path_heads
Revises:
- 20260611_030000_merge_dashboard_guest_and_request_log_client_ip_heads
- 20260629_000000_add_dashboard_query_hot_path_indexes
Create Date: 2026-06-30 00:00:00.000000
"""

from __future__ import annotations

revision = "20260630_000000_merge_request_log_client_ip_and_dashboard_hot_path_heads"
down_revision = (
    "20260611_030000_merge_dashboard_guest_and_request_log_client_ip_heads",
    "20260629_000000_add_dashboard_query_hot_path_indexes",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
