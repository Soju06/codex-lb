"""add automation run cycle metadata columns

Revision ID: 20260419_000000_add_automation_run_cycle_metadata
Revises: 20260418_190000_add_automation_runs_cycle_key
Create Date: 2026-04-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260419_000000_add_automation_run_cycle_metadata"
down_revision = "20260418_190000_add_automation_runs_cycle_key"
branch_labels = None
depends_on = None


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def _column_names(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "automation_runs"):
        return

    run_columns = _column_names(bind, "automation_runs")
    if "cycle_expected_accounts" not in run_columns:
        op.add_column("automation_runs", sa.Column("cycle_expected_accounts", sa.Integer(), nullable=True))
    if "cycle_window_end" not in run_columns:
        op.add_column("automation_runs", sa.Column("cycle_window_end", sa.DateTime(), nullable=True))

    op.execute("UPDATE automation_runs SET cycle_expected_accounts = 0 WHERE cycle_expected_accounts IS NULL")
    op.execute("UPDATE automation_runs SET cycle_window_end = scheduled_for WHERE cycle_window_end IS NULL")


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "automation_runs"):
        return

    run_columns = _column_names(bind, "automation_runs")
    if "cycle_window_end" in run_columns:
        op.drop_column("automation_runs", "cycle_window_end")
    if "cycle_expected_accounts" in run_columns:
        op.drop_column("automation_runs", "cycle_expected_accounts")
