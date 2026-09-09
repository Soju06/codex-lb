"""Persist telemetry v2 notice and completed-day acknowledgement."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260909_070000_expand_telemetry_v2"
down_revision = "20260909_060000_add_report_rollup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dashboard_settings",
        sa.Column("telemetry_notice_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "dashboard_settings", sa.Column("telemetry_day_acknowledged_date", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("dashboard_settings", "telemetry_day_acknowledged_date")
    op.drop_column("dashboard_settings", "telemetry_notice_version")
