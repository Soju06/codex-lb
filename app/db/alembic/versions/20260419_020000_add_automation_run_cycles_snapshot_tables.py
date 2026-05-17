"""add automation run cycle snapshot tables

Revision ID: 20260419_020000_add_automation_run_cycles_snapshot_tables
Revises: 20260419_000000_add_automation_run_cycle_metadata
Create Date: 2026-04-19
"""

from __future__ import annotations

from datetime import datetime
from typing import TypedDict

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260419_020000_add_automation_run_cycles_snapshot_tables"
down_revision = "20260419_000000_add_automation_run_cycle_metadata"
branch_labels = None
depends_on = None


class _ObservedRunRow(TypedDict):
    cycle_key: str
    job_id: str
    trigger: str
    account_id: str | None
    scheduled_for: datetime
    cycle_window_end: datetime | None
    created_at: datetime


class _ObservedCycleSnapshot(TypedDict):
    cycle_key: str
    job_id: str
    trigger: str
    cycle_expected_accounts: int
    cycle_window_end: datetime | None
    created_at: datetime
    accounts: list[tuple[str, datetime]]


class _MutableCycleSnapshot(TypedDict):
    job_id: str
    trigger: str
    cycle_window_end: datetime | None
    created_at: datetime
    accounts: dict[str, datetime]


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def _column_names(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name")}


def _normalize_legacy_manual_cycle_key(value: str) -> str | None:
    parts = value.split(":")
    if len(parts) == 3 and parts[0] == "manual" and parts[1] and parts[2]:
        return value
    if len(parts) == 4 and parts[0] == "manual" and parts[1] and parts[2]:
        return f"manual:{parts[1]}:{parts[2]}"
    return None


def _normalize_cycle_key(*, trigger: str, cycle_key: str, slot_key: str) -> str:
    if trigger == "manual":
        normalized_cycle_key = _normalize_legacy_manual_cycle_key(cycle_key)
        if normalized_cycle_key is not None:
            return normalized_cycle_key
        normalized_slot_cycle_key = _normalize_legacy_manual_cycle_key(slot_key)
        if normalized_slot_cycle_key is not None:
            return normalized_slot_cycle_key
    return cycle_key


def _new_mutable_cycle_snapshot(row: _ObservedRunRow) -> _MutableCycleSnapshot:
    return {
        "job_id": row["job_id"],
        "trigger": row["trigger"],
        "cycle_window_end": row["cycle_window_end"] or row["scheduled_for"],
        "created_at": row["created_at"],
        "accounts": {},
    }


def _build_cycle_snapshots(rows: list[_ObservedRunRow]) -> list[_ObservedCycleSnapshot]:
    snapshots: dict[str, _MutableCycleSnapshot] = {}
    for row in rows:
        snapshot = snapshots.setdefault(
            row["cycle_key"],
            _new_mutable_cycle_snapshot(row),
        )
        cycle_window_end = snapshot["cycle_window_end"]
        if cycle_window_end is None or (
            row["cycle_window_end"] is not None and row["cycle_window_end"] > cycle_window_end
        ):
            snapshot["cycle_window_end"] = row["cycle_window_end"]
        elif cycle_window_end is None or row["scheduled_for"] > cycle_window_end:
            snapshot["cycle_window_end"] = row["scheduled_for"]

        if row["created_at"] < snapshot["created_at"]:
            snapshot["created_at"] = row["created_at"]

        account_id = row["account_id"]
        if account_id is None:
            continue
        scheduled_for = snapshot["accounts"].get(account_id)
        if scheduled_for is None or row["scheduled_for"] < scheduled_for:
            snapshot["accounts"][account_id] = row["scheduled_for"]

    normalized_snapshots: list[_ObservedCycleSnapshot] = []
    for cycle_key, snapshot in snapshots.items():
        account_rows = sorted(
            snapshot["accounts"].items(),
            key=lambda item: (item[1], item[0]),
        )
        normalized_snapshots.append(
            {
                "cycle_key": cycle_key,
                "job_id": snapshot["job_id"],
                "trigger": snapshot["trigger"],
                "cycle_expected_accounts": len(account_rows),
                "cycle_window_end": snapshot["cycle_window_end"],
                "created_at": snapshot["created_at"],
                "accounts": account_rows,
            }
        )
    return sorted(normalized_snapshots, key=lambda snapshot: snapshot["cycle_key"])


def _create_cycle_tables(connection: Connection) -> None:
    if not _table_exists(connection, "automation_run_cycles"):
        op.create_table(
            "automation_run_cycles",
            sa.Column("cycle_key", sa.String(length=160), nullable=False),
            sa.Column("job_id", sa.String(), nullable=False),
            sa.Column("trigger", sa.String(length=16), nullable=False),
            sa.Column(
                "cycle_expected_accounts",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column("cycle_window_end", sa.DateTime(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(["job_id"], ["automation_jobs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("cycle_key"),
        )

    if not _table_exists(connection, "automation_run_cycle_accounts"):
        op.create_table(
            "automation_run_cycle_accounts",
            sa.Column("cycle_key", sa.String(length=160), nullable=False),
            sa.Column("account_id", sa.String(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("scheduled_for", sa.DateTime(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(
                ["cycle_key"],
                ["automation_run_cycles.cycle_key"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("cycle_key", "account_id"),
            sa.UniqueConstraint("cycle_key", "position", name="uq_automation_run_cycle_accounts_position"),
        )


def _backfill_cycle_tables(connection: Connection) -> None:
    run_columns = _column_names(connection, "automation_runs")
    required_columns = {
        "cycle_key",
        "slot_key",
        "job_id",
        "trigger",
        "account_id",
        "scheduled_for",
        "cycle_window_end",
        "created_at",
    }
    if not required_columns.issubset(run_columns):
        return

    observed_rows = connection.execute(
        sa.text(
            """
            SELECT
                cycle_key,
                slot_key,
                job_id,
                trigger,
                account_id,
                scheduled_for,
                cycle_window_end,
                created_at
            FROM automation_runs
            WHERE cycle_key IS NOT NULL AND cycle_key != ''
            ORDER BY created_at ASC, scheduled_for ASC, id ASC
            """
        )
    ).mappings()
    snapshots = _build_cycle_snapshots(
        [
            {
                "cycle_key": _normalize_cycle_key(
                    trigger=str(row["trigger"]),
                    cycle_key=str(row["cycle_key"]),
                    slot_key=str(row["slot_key"]),
                ),
                "job_id": str(row["job_id"]),
                "trigger": str(row["trigger"]),
                "account_id": str(row["account_id"]) if row["account_id"] else None,
                "scheduled_for": row["scheduled_for"],
                "cycle_window_end": row["cycle_window_end"],
                "created_at": row["created_at"],
            }
            for row in observed_rows
        ]
    )

    existing_cycle_keys = set(
        connection.execute(sa.text("SELECT cycle_key FROM automation_run_cycles")).scalars().all()
    )
    for snapshot in snapshots:
        if snapshot["cycle_key"] in existing_cycle_keys:
            continue
        connection.execute(
            sa.text(
                """
                INSERT INTO automation_run_cycles (
                    cycle_key,
                    job_id,
                    trigger,
                    cycle_expected_accounts,
                    cycle_window_end,
                    created_at
                ) VALUES (
                    :cycle_key,
                    :job_id,
                    :trigger,
                    :cycle_expected_accounts,
                    :cycle_window_end,
                    :created_at
                )
                """
            ),
            {
                "cycle_key": snapshot["cycle_key"],
                "job_id": snapshot["job_id"],
                "trigger": snapshot["trigger"],
                "cycle_expected_accounts": snapshot["cycle_expected_accounts"],
                "cycle_window_end": snapshot["cycle_window_end"],
                "created_at": snapshot["created_at"],
            },
        )
        existing_cycle_keys.add(snapshot["cycle_key"])

    existing_cycle_accounts = {
        (cycle_key, account_id)
        for cycle_key, account_id in connection.execute(
            sa.text("SELECT cycle_key, account_id FROM automation_run_cycle_accounts")
        ).all()
    }
    for snapshot in snapshots:
        for position, (account_id, scheduled_for) in enumerate(snapshot["accounts"]):
            account_key = (snapshot["cycle_key"], account_id)
            if account_key in existing_cycle_accounts:
                continue
            connection.execute(
                sa.text(
                    """
                    INSERT INTO automation_run_cycle_accounts (
                        cycle_key,
                        account_id,
                        position,
                        scheduled_for
                    ) VALUES (
                        :cycle_key,
                        :account_id,
                        :position,
                        :scheduled_for
                    )
                    """
                ),
                {
                    "cycle_key": snapshot["cycle_key"],
                    "account_id": account_id,
                    "position": position,
                    "scheduled_for": scheduled_for,
                },
            )
            existing_cycle_accounts.add(account_key)


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "automation_runs"):
        return

    _create_cycle_tables(bind)
    _backfill_cycle_tables(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "automation_run_cycle_accounts"):
        op.drop_table("automation_run_cycle_accounts")
    if _table_exists(bind, "automation_run_cycles"):
        op.drop_table("automation_run_cycles")
