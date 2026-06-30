"""merge warmup staggered idle and request-log dashboard heads

Revision ID: 20260630_010000_merge_warmup_and_request_log_dashboard_heads
Revises:
- 20260630_000000_add_limit_warmup_staggered_idle
- 20260630_000000_merge_request_log_client_ip_and_dashboard_hot_path_heads
Create Date: 2026-06-30 01:00:00.000000
"""

from __future__ import annotations

revision = "20260630_010000_merge_warmup_and_request_log_dashboard_heads"
down_revision = (
    "20260630_000000_add_limit_warmup_staggered_idle",
    "20260630_000000_merge_request_log_client_ip_and_dashboard_hot_path_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
