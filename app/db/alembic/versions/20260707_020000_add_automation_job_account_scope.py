"""add automation job account scope flag

Revision ID: 20260707_020000_add_automation_job_account_scope
Revises: 20260707_010000_merge_automations_and_model_sources_heads
Create Date: 2026-07-07
"""

from __future__ import annotations

from datetime import datetime
from hashlib import sha1

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260707_020000_add_automation_job_account_scope"
down_revision = "20260707_010000_merge_automations_and_model_sources_heads"
branch_labels = None
depends_on = None


def _column_names(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name")}


def _scheduled_slot_key(job_id: str, *, account_id: str, due_slot: datetime) -> str:
    seed = f"{job_id}:{due_slot.isoformat()}:{account_id}"
    digest = sha1(seed.encode("utf-8")).hexdigest()[:20]
    return f"scheduled:{job_id}:{digest}"


def _parse_scheduled_cycle_due_slot(cycle_key: str, *, job_id: str) -> datetime | None:
    parts = cycle_key.split(":", maxsplit=2)
    if len(parts) != 3 or parts[0] != "scheduled" or parts[1] != job_id:
        return None
    try:
        return datetime.fromisoformat(parts[2].removesuffix("Z"))
    except ValueError:
        return None


def _coerce_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"Unsupported scheduled_for value: {value!r}")


def _backfill_cycle_account_slot_keys(connection: Connection) -> None:
    rows = connection.execute(
        sa.text(
            """
            SELECT accounts.cycle_key, cycles.job_id, accounts.account_id, accounts.scheduled_for
            FROM automation_run_cycle_accounts AS accounts
            JOIN automation_run_cycles AS cycles ON cycles.cycle_key = accounts.cycle_key
            WHERE cycles.trigger = 'scheduled'
              AND accounts.slot_key IS NULL
            """
        )
    ).mappings()
    for row in rows:
        job_id = str(row["job_id"])
        due_slot = _parse_scheduled_cycle_due_slot(str(row["cycle_key"]), job_id=job_id)
        if due_slot is None:
            due_slot = _coerce_datetime(row["scheduled_for"])
        slot_key = _scheduled_slot_key(
            job_id,
            account_id=str(row["account_id"]),
            due_slot=due_slot,
        )
        connection.execute(
            sa.text(
                """
                UPDATE automation_run_cycle_accounts
                SET slot_key = :slot_key
                WHERE cycle_key = :cycle_key
                  AND account_id = :account_id
                """
            ),
            {
                "slot_key": slot_key,
                "cycle_key": row["cycle_key"],
                "account_id": row["account_id"],
            },
        )


def upgrade() -> None:
    connection = op.get_bind()
    if "account_scope_all" not in _column_names(connection, "automation_jobs"):
        op.add_column(
            "automation_jobs",
            sa.Column("account_scope_all", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
    op.execute(
        sa.text(
            """
            UPDATE automation_jobs
            SET account_scope_all = false
            WHERE id IN (
                SELECT DISTINCT job_id
                FROM automation_job_accounts
            )
            """
        )
    )
    if "slot_key" not in _column_names(connection, "automation_run_cycle_accounts"):
        op.add_column(
            "automation_run_cycle_accounts",
            sa.Column("slot_key", sa.String(length=128), nullable=True),
        )
    _backfill_cycle_account_slot_keys(connection)


def downgrade() -> None:
    connection = op.get_bind()
    if "slot_key" in _column_names(connection, "automation_run_cycle_accounts"):
        op.drop_column("automation_run_cycle_accounts", "slot_key")
    if "account_scope_all" in _column_names(connection, "automation_jobs"):
        op.drop_column("automation_jobs", "account_scope_all")
