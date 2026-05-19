"""merge HTTP bridge durable input and sqlite recovery heads

Revision ID: 20260518_010000_merge_http_bridge_and_sqlite_recovery_heads
Revises: 20260517_000000_merge_sqlite_hot_path_and_request_log_delete_heads,
    20260518_000000_add_http_bridge_durable_input_prefix
Create Date: 2026-05-18
"""

from __future__ import annotations

revision = "20260518_010000_merge_http_bridge_and_sqlite_recovery_heads"
down_revision = (
    "20260517_000000_merge_sqlite_hot_path_and_request_log_delete_heads",
    "20260518_000000_add_http_bridge_durable_input_prefix",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
