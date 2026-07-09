"""add cycle key to automation runs

Revision ID: 20260418_190000_add_automation_runs_cycle_key
Revises: 20260415_160000_add_automations_tables
Create Date: 2026-04-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260418_190000_add_automation_runs_cycle_key"
down_revision = "20260415_160000_add_automations_tables"
branch_labels = None
depends_on = None


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def _index_names(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(index["name"]) for index in inspector.get_indexes(table_name) if index.get("name")}


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
    if "cycle_key" not in run_columns:
        op.add_column(
            "automation_runs",
            sa.Column("cycle_key", sa.String(length=160), nullable=True),
        )
        op.execute("UPDATE automation_runs SET cycle_key = slot_key WHERE cycle_key IS NULL OR cycle_key = ''")
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table("automation_runs", recreate="always") as batch_op:
                batch_op.alter_column(
                    "cycle_key",
                    existing_type=sa.String(length=160),
                    nullable=False,
                )
        else:
            op.alter_column(
                "automation_runs",
                "cycle_key",
                existing_type=sa.String(length=160),
                nullable=False,
            )

    run_indexes = _index_names(bind, "automation_runs")
    if "idx_automation_runs_cycle_key_started_at" not in run_indexes:
        op.create_index(
            "idx_automation_runs_cycle_key_started_at",
            "automation_runs",
            ["cycle_key", "started_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "automation_runs"):
        return

    run_indexes = _index_names(bind, "automation_runs")
    if "idx_automation_runs_cycle_key_started_at" in run_indexes:
        op.drop_index("idx_automation_runs_cycle_key_started_at", table_name="automation_runs")

    run_columns = _column_names(bind, "automation_runs")
    if "cycle_key" in run_columns:
        op.drop_column("automation_runs", "cycle_key")
