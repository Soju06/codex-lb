"""merge request log tier and dashboard index heads

Revision ID: 20260321_210000_merge_request_log_tiers_and_dashboard_index_heads
Revises: 20260320_000000_add_request_log_requested_actual_tiers, 20260321_190000_tighten_dashboard_db_indexes
Create Date: 2026-03-21
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260321_210000_merge_request_log_tiers_and_dashboard_index_heads"
down_revision = (
    "20260320_000000_add_request_log_requested_actual_tiers",
    "20260321_190000_tighten_dashboard_db_indexes",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
