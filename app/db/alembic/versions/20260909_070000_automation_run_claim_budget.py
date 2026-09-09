"""pin the automation stale-claim reclaim window to the budget captured at claim time

Revision ID: 20260909_070000_automation_run_claim_budget
Revises: 20260909_060000_add_report_rollup
Create Date: 2026-09-09

Adds the nullable ``automation_runs.claim_budget_seconds`` column. Every
claim (fresh or stale reclaim) stores the compact request budget in effect
at that moment, and the stale-claim reclaim window is derived from the
stored value instead of the current dashboard budget, so lowering the
budget in the dashboard no longer reclaims an in-flight run early. Rows
claimed before this revision stay NULL and keep using the current budget.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260909_070000_automation_run_claim_budget"
down_revision = "20260909_060000_add_report_rollup"
branch_labels = None
depends_on = None

_TABLE = "automation_runs"
_COLUMN = "claim_budget_seconds"


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    existing = _columns(op.get_bind(), _TABLE)
    if not existing or _COLUMN in existing:
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.add_column(sa.Column(_COLUMN, sa.Float(), nullable=True))


def downgrade() -> None:
    existing = _columns(op.get_bind(), _TABLE)
    if _COLUMN not in existing:
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_column(_COLUMN)
