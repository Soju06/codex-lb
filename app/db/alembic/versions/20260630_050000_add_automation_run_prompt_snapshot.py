"""add automation run prompt snapshots

Revision ID: 20260630_050000_add_automation_run_prompt_snapshot
Revises: 20260630_040000_merge_automation_snapshot_and_warmup_threshold_heads
Create Date: 2026-06-30 05:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260630_050000_add_automation_run_prompt_snapshot"
down_revision = "20260630_040000_merge_automation_snapshot_and_warmup_threshold_heads"
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
    if "prompt" not in run_columns:
        with op.batch_alter_table("automation_runs") as batch_op:
            batch_op.add_column(sa.Column("prompt", sa.Text(), nullable=True))

    op.execute(
        """
        UPDATE automation_runs
        SET prompt = COALESCE(automation_runs.prompt, automation_jobs.prompt)
        FROM automation_jobs
        WHERE automation_jobs.id = automation_runs.job_id
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    run_columns = _columns(bind, "automation_runs")
    if "prompt" not in run_columns:
        return
    with op.batch_alter_table("automation_runs") as batch_op:
        batch_op.drop_column("prompt")
