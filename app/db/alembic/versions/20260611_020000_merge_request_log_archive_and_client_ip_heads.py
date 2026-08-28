"""merge request log archive request id and client ip heads

Revision ID: 20260611_020000_merge_request_log_archive_and_client_ip_heads
Revises:
- 20260611_000000_add_request_log_archive_request_id
- 20260611_010000_add_request_log_client_ip
Create Date: 2026-06-11 02:00:00.000000
"""

from __future__ import annotations

revision = "20260611_020000_merge_request_log_archive_and_client_ip_heads"
down_revision = (
    "20260611_000000_add_request_log_archive_request_id",
    "20260611_010000_add_request_log_client_ip",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
