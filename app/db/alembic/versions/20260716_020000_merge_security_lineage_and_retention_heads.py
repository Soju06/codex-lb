"""merge security-lineage and retention heads

Revision ID: 20260716_020000_merge_security_lineage_and_retention_heads
Revises: 20260711_030000_detach_security_lineage_markers, 20260716_010000_add_dashboard_retention_settings
Create Date: 2026-07-16 02:00:00.000000
"""

from __future__ import annotations

revision = "20260716_020000_merge_security_lineage_and_retention_heads"
down_revision = (
    "20260711_030000_detach_security_lineage_markers",
    "20260716_010000_add_dashboard_retention_settings",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
