"""Merge the usage-cap repair and live request-log facet heads.

Revision ID: 20260910_000000_merge_usage_cap_repair_and_live_facets
Revises: 20260909_090000_repair_reparented_overflow_schema, 20260909_130000_add_request_logs_live_facet_indexes
Create Date: 2026-09-10
"""

from __future__ import annotations

revision = "20260910_000000_merge_usage_cap_repair_and_live_facets"
down_revision = (
    "20260909_090000_repair_reparented_overflow_schema",
    "20260909_130000_add_request_logs_live_facet_indexes",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
