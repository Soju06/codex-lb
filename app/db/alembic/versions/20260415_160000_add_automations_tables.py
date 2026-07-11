"""add automations tables

Revision ID: 20260415_160000_add_automations_tables
Revises: 20260415_160000_add_request_logs_response_lookup_index
Create Date: 2026-04-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260415_160000_add_automations_tables"
down_revision = "20260415_160000_add_request_logs_response_lookup_index"
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

    if not _table_exists(bind, "automation_jobs"):
        op.create_table(
            "automation_jobs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("schedule_type", sa.String(length=32), nullable=False, server_default=sa.text("'daily'")),
            sa.Column("schedule_time", sa.String(length=5), nullable=False),
            sa.Column("schedule_timezone", sa.String(length=64), nullable=False),
            sa.Column(
                "schedule_days",
                sa.String(length=64),
                nullable=False,
                server_default=sa.text("'mon,tue,wed,thu,fri,sat,sun'"),
            ),
            sa.Column("schedule_threshold_minutes", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("include_paused_accounts", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("model", sa.String(), nullable=False),
            sa.Column("reasoning_effort", sa.String(length=16), nullable=True),
            sa.Column("prompt", sa.Text(), nullable=False, server_default=sa.text("'ping'")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
        )
    else:
        job_columns = _column_names(bind, "automation_jobs")
        if "schedule_days" not in job_columns:
            op.add_column(
                "automation_jobs",
                sa.Column(
                    "schedule_days",
                    sa.String(length=64),
                    nullable=False,
                    server_default=sa.text("'mon,tue,wed,thu,fri,sat,sun'"),
                ),
            )
        if "reasoning_effort" not in job_columns:
            op.add_column(
                "automation_jobs",
                sa.Column("reasoning_effort", sa.String(length=16), nullable=True),
            )
        if "schedule_threshold_minutes" not in job_columns:
            op.add_column(
                "automation_jobs",
                sa.Column("schedule_threshold_minutes", sa.Integer(), nullable=False, server_default=sa.text("0")),
            )
        if "include_paused_accounts" not in job_columns:
            op.add_column(
                "automation_jobs",
                sa.Column("include_paused_accounts", sa.Boolean(), nullable=False, server_default=sa.false()),
            )

    if not _table_exists(bind, "automation_job_accounts"):
        op.create_table(
            "automation_job_accounts",
            sa.Column("job_id", sa.String(), nullable=False),
            sa.Column("account_id", sa.String(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["job_id"], ["automation_jobs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("job_id", "account_id"),
            sa.UniqueConstraint("job_id", "position", name="uq_automation_job_accounts_position"),
        )

    if not _table_exists(bind, "automation_runs"):
        op.create_table(
            "automation_runs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("job_id", sa.String(), nullable=False),
            sa.Column("trigger", sa.String(length=16), nullable=False),
            sa.Column("slot_key", sa.String(length=128), nullable=False),
            sa.Column("scheduled_for", sa.DateTime(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default=sa.text("'running'")),
            sa.Column("account_id", sa.String(), nullable=True),
            sa.Column("error_code", sa.String(length=100), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["job_id"], ["automation_jobs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("slot_key", name="uq_automation_runs_slot_key"),
        )

    jobs_indexes = _index_names(bind, "automation_jobs")
    if "idx_automation_jobs_enabled" not in jobs_indexes:
        op.create_index("idx_automation_jobs_enabled", "automation_jobs", ["enabled"], unique=False)

    job_accounts_indexes = _index_names(bind, "automation_job_accounts")
    if "idx_automation_job_accounts_account_id" not in job_accounts_indexes:
        op.create_index(
            "idx_automation_job_accounts_account_id",
            "automation_job_accounts",
            ["account_id"],
            unique=False,
        )

    runs_indexes = _index_names(bind, "automation_runs")
    if "idx_automation_runs_job_id_started_at" not in runs_indexes:
        op.create_index(
            "idx_automation_runs_job_id_started_at",
            "automation_runs",
            ["job_id", "started_at"],
            unique=False,
        )
    if "idx_automation_runs_status_started_at" not in runs_indexes:
        op.create_index(
            "idx_automation_runs_status_started_at",
            "automation_runs",
            ["status", "started_at"],
            unique=False,
        )
    if "idx_automation_runs_scheduled_for" not in runs_indexes:
        op.create_index(
            "idx_automation_runs_scheduled_for",
            "automation_runs",
            ["scheduled_for"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()

    if _table_exists(bind, "automation_runs"):
        runs_indexes = _index_names(bind, "automation_runs")
        if "idx_automation_runs_scheduled_for" in runs_indexes:
            op.drop_index("idx_automation_runs_scheduled_for", table_name="automation_runs")
        if "idx_automation_runs_status_started_at" in runs_indexes:
            op.drop_index("idx_automation_runs_status_started_at", table_name="automation_runs")
        if "idx_automation_runs_job_id_started_at" in runs_indexes:
            op.drop_index("idx_automation_runs_job_id_started_at", table_name="automation_runs")
        op.drop_table("automation_runs")

    if _table_exists(bind, "automation_job_accounts"):
        job_accounts_indexes = _index_names(bind, "automation_job_accounts")
        if "idx_automation_job_accounts_account_id" in job_accounts_indexes:
            op.drop_index("idx_automation_job_accounts_account_id", table_name="automation_job_accounts")
        op.drop_table("automation_job_accounts")

    if _table_exists(bind, "automation_jobs"):
        jobs_indexes = _index_names(bind, "automation_jobs")
        if "idx_automation_jobs_enabled" in jobs_indexes:
            op.drop_index("idx_automation_jobs_enabled", table_name="automation_jobs")
        op.drop_table("automation_jobs")
