"""add ttft phase observability request log fields

Revision ID: 20260709_000000_add_ttft_phase_observability
Revises: 20260707_020000_add_automation_job_account_scope
Create Date: 2026-07-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260709_000000_add_ttft_phase_observability"
down_revision = "20260707_020000_add_automation_job_account_scope"
branch_labels = None
depends_on = None

_COLUMN_NAMES = (
    "latency_response_created_ms",
    "latency_first_upstream_event_ms",
    "latency_response_create_gate_wait_ms",
    "latency_bridge_queue_wait_ms",
    "prewarm_status",
    "prewarm_latency_ms",
    "prewarm_canary_bucket",
    "prewarm_eligible_reason",
    "session_previous_gap_ms",
)


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind, "request_logs")
    if not existing:
        return
    with op.batch_alter_table("request_logs") as batch_op:
        for column_name in _COLUMN_NAMES:
            if column_name in existing:
                continue
            if column_name in {
                "prewarm_status",
                "prewarm_canary_bucket",
                "prewarm_eligible_reason",
            }:
                batch_op.add_column(sa.Column(column_name, sa.String(), nullable=True))
            else:
                batch_op.add_column(sa.Column(column_name, sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind, "request_logs")
    if not existing:
        return
    with op.batch_alter_table("request_logs") as batch_op:
        for column_name in reversed(_COLUMN_NAMES):
            if column_name in existing:
                batch_op.drop_column(column_name)
