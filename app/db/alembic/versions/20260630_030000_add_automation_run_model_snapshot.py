"""add automation run model snapshots

Revision ID: 20260630_030000_add_automation_run_model_snapshot
Revises: 20260630_020000_merge_automations_and_main_heads
Create Date: 2026-06-30 03:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260630_030000_add_automation_run_model_snapshot"
down_revision = "20260630_020000_merge_automations_and_main_heads"
branch_labels = None
depends_on = None


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    run_columns = _columns(bind, "automation_runs")
    if not run_columns:
        return
    with op.batch_alter_table("automation_runs") as batch_op:
        if "model" not in run_columns:
            batch_op.add_column(sa.Column("model", sa.String(), nullable=True))
        if "reasoning_effort" not in run_columns:
            batch_op.add_column(sa.Column("reasoning_effort", sa.String(length=16), nullable=True))

    op.execute(
        """
        UPDATE automation_runs
        SET
            model = COALESCE(automation_runs.model, automation_jobs.model),
            reasoning_effort = COALESCE(automation_runs.reasoning_effort, automation_jobs.reasoning_effort)
        FROM automation_jobs
        WHERE automation_jobs.id = automation_runs.job_id
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    run_columns = _columns(bind, "automation_runs")
    if "model" not in run_columns and "reasoning_effort" not in run_columns:
        return
    with op.batch_alter_table("automation_runs") as batch_op:
        if "reasoning_effort" in run_columns:
            batch_op.drop_column("reasoning_effort")
        if "model" in run_columns:
            batch_op.drop_column("model")
