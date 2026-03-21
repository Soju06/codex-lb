"""merge http bridge lease head

Revision ID: 20260322_000000_merge_http_bridge_lease_head
Revises: 20260321_120000_add_http_bridge_leases, 20260321_210000_merge_request_log_tiers_and_dashboard_index_heads
Create Date: 2026-03-22
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260322_000000_merge_http_bridge_lease_head"
down_revision = (
    "20260321_120000_add_http_bridge_leases",
    "20260321_210000_merge_request_log_tiers_and_dashboard_index_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
