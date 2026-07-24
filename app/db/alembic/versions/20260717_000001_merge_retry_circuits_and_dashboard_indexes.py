"""merge retry circuits and dashboard index heads

Revision ID: 20260717_000001_merge_retry_circuits_and_dashboard_indexes
Revises:
- 20260717_000000_add_http_bridge_retry_circuits
- 20260717_000000_optimize_dashboard_hot_path_indexes
Create Date: 2026-07-17 00:00:00.000000
"""

from __future__ import annotations

revision = "20260717_000001_merge_retry_circuits_and_dashboard_indexes"
down_revision = (
    "20260717_000000_add_http_bridge_retry_circuits",
    "20260717_000000_optimize_dashboard_hot_path_indexes",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
