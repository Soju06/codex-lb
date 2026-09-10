"""Persist telemetry v2 notice and completed-day acknowledgement.

Revision ID: 20260910_010000_expand_telemetry_v2
Revises: 20260910_000000_request_logs_missing_cost_index
Create Date: 2026-09-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260910_010000_expand_telemetry_v2"
down_revision = "20260910_000000_request_logs_missing_cost_index"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("dashboard_settings")}


def upgrade() -> None:
    # Idempotent like 20260806_000000_add_anonymous_telemetry: the legacy
    # revision-id remap path re-applies head on an already-upgraded database.
    columns = _columns()
    with op.batch_alter_table("dashboard_settings") as batch_op:
        if "telemetry_notice_version" not in columns:
            batch_op.add_column(sa.Column("telemetry_notice_version", sa.Integer(), nullable=True))
        if "telemetry_day_acknowledged_date" not in columns:
            batch_op.add_column(sa.Column("telemetry_day_acknowledged_date", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    columns = _columns()
    with op.batch_alter_table("dashboard_settings") as batch_op:
        if "telemetry_day_acknowledged_date" in columns:
            batch_op.drop_column("telemetry_day_acknowledged_date")
        if "telemetry_notice_version" in columns:
            batch_op.drop_column("telemetry_notice_version")
