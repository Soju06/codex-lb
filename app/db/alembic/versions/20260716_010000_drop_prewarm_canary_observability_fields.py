"""drop prewarm canary observability request log fields

Phase 4 of the settings-surface reduction (issue #1340) retires the Codex
prewarm canary scaffolding: prewarm eligibility is the enabled flag alone,
so the per-request canary bucket and eligibility cohort are no longer
recorded.

Revision ID: 20260716_010000_drop_prewarm_canary_observability_fields
Revises: 20260716_000000_add_oauth_device_flow_slots
Create Date: 2026-07-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260716_010000_drop_prewarm_canary_observability_fields"
down_revision = "20260716_000000_add_oauth_device_flow_slots"
branch_labels = None
depends_on = None

_COLUMN_NAMES = (
    "prewarm_canary_bucket",
    "prewarm_eligible_reason",
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
                batch_op.drop_column(column_name)


def downgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind, "request_logs")
    if not existing:
        return
    with op.batch_alter_table("request_logs") as batch_op:
        for column_name in _COLUMN_NAMES:
            if column_name not in existing:
                batch_op.add_column(sa.Column(column_name, sa.String(), nullable=True))
