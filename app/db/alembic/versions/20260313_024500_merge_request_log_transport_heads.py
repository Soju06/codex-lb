"""merge request-log transport heads

Revision ID: 20260313_024500_merge_request_log_transport_heads
Revises:
    20260312_020000_merge_request_logs_transport_and_feature_heads,
    20260312_020000_merge_request_logs_transport_and_sticky_heads
Create Date: 2026-03-13 02:45:00.000000
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260313_024500_merge_request_log_transport_heads"
down_revision = (
    "20260312_020000_merge_request_logs_transport_and_feature_heads",
    "20260312_020000_merge_request_logs_transport_and_sticky_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    return


def downgrade() -> None:
    return
