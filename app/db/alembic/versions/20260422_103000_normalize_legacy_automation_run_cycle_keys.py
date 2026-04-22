"""normalize legacy automation run cycle keys

Revision ID: 20260422_103000_normalize_legacy_automation_run_cycle_keys
Revises: 20260421_130000_merge_automation_and_request_log_heads
Create Date: 2026-04-22
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TypedDict

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260422_103000_normalize_legacy_automation_run_cycle_keys"
down_revision = "20260421_130000_merge_automation_and_request_log_heads"
branch_labels = None
depends_on = None


class _ObservedRunRow(TypedDict):
    id: str
    cycle_key: str
    slot_key: str
    job_id: str
    trigger: str
    account_id: str | None
    scheduled_for: datetime
    cycle_window_end: datetime | None
    cycle_expected_accounts: int | None
    created_at: datetime
    schedule_threshold_minutes: int | None


class _NormalizedRunRow(TypedDict):
    id: str
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


def _coerce_datetime(value: datetime | str | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _looks_like_legacy_scheduled_digest(value: str, *, job_id: str) -> bool:
    parts = value.split(":")
    if len(parts) != 3:
        return False
    trigger, parsed_job_id, digest = parts
    if trigger != "scheduled" or parsed_job_id != job_id:
        return False
    if len(digest) != 20:
        return False
    return all(character in "0123456789abcdef" for character in digest)


def _normalize_cycle_key(row: _ObservedRunRow) -> str:
    trigger = row["trigger"]
    cycle_key = row["cycle_key"]
    slot_key = row["slot_key"]
    if trigger == "manual":
        normalized_cycle_key = _normalize_legacy_manual_cycle_key(cycle_key)
        if normalized_cycle_key is not None:
            return normalized_cycle_key
        normalized_slot_cycle_key = _normalize_legacy_manual_cycle_key(slot_key)
        if normalized_slot_cycle_key is not None:
            return normalized_slot_cycle_key
        return cycle_key
    if trigger == "scheduled" and _looks_like_legacy_scheduled_digest(cycle_key, job_id=row["job_id"]):
        threshold_minutes = max(0, row["schedule_threshold_minutes"] or 0)
        cycle_anchor = row["cycle_window_end"] or row["scheduled_for"]
        due_slot = cycle_anchor - timedelta(minutes=threshold_minutes)
        return f"scheduled:{row['job_id']}:{due_slot.isoformat()}"
    return cycle_key


def _new_mutable_cycle_snapshot(row: _NormalizedRunRow) -> _MutableCycleSnapshot:
    return {
        "job_id": row["job_id"],
        "trigger": row["trigger"],
        "cycle_window_end": row["cycle_window_end"] or row["scheduled_for"],
        "created_at": row["created_at"],
        "accounts": {},
    }


def _build_cycle_snapshots(rows: list[_NormalizedRunRow]) -> list[_ObservedCycleSnapshot]:
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


def _load_observed_runs(connection: Connection) -> list[_ObservedRunRow]:
    run_columns = _column_names(connection, "automation_runs")
    required_columns = {
        "id",
        "cycle_key",
        "slot_key",
        "job_id",
        "trigger",
        "account_id",
        "scheduled_for",
        "cycle_window_end",
        "cycle_expected_accounts",
        "created_at",
    }
    if not required_columns.issubset(run_columns):
        return []

    observed_rows = connection.execute(
        sa.text(
            """
            SELECT
                automation_runs.id,
                automation_runs.cycle_key,
                automation_runs.slot_key,
                automation_runs.job_id,
                automation_runs.trigger,
                automation_runs.account_id,
                automation_runs.scheduled_for,
                automation_runs.cycle_window_end,
                automation_runs.cycle_expected_accounts,
                automation_runs.created_at,
                automation_jobs.schedule_threshold_minutes
            FROM automation_runs
            JOIN automation_jobs ON automation_jobs.id = automation_runs.job_id
            WHERE automation_runs.cycle_key IS NOT NULL AND automation_runs.cycle_key != ''
            ORDER BY automation_runs.created_at ASC, automation_runs.scheduled_for ASC, automation_runs.id ASC
            """
        )
    ).mappings()
    normalized_rows: list[_ObservedRunRow] = []
    for row in observed_rows:
        scheduled_for = _coerce_datetime(row["scheduled_for"])
        created_at = _coerce_datetime(row["created_at"])
        assert scheduled_for is not None
        assert created_at is not None
        cycle_expected_accounts = row["cycle_expected_accounts"]
        normalized_rows.append(
            {
                "id": str(row["id"]),
                "cycle_key": str(row["cycle_key"]),
                "slot_key": str(row["slot_key"]),
                "job_id": str(row["job_id"]),
                "trigger": str(row["trigger"]),
                "account_id": str(row["account_id"]) if row["account_id"] else None,
                "scheduled_for": scheduled_for,
                "cycle_window_end": _coerce_datetime(row["cycle_window_end"]),
                "cycle_expected_accounts": (
                    int(cycle_expected_accounts) if cycle_expected_accounts is not None else None
                ),
                "created_at": created_at,
                "schedule_threshold_minutes": (
                    int(row["schedule_threshold_minutes"]) if row["schedule_threshold_minutes"] is not None else None
                ),
            }
        )
    return normalized_rows


def _normalize_runs(connection: Connection, rows: list[_ObservedRunRow]) -> list[_NormalizedRunRow]:
    normalized_rows: list[_NormalizedRunRow] = []
    for row in rows:
        normalized_rows.append(
            {
                "id": row["id"],
                "cycle_key": _normalize_cycle_key(row),
                "job_id": row["job_id"],
                "trigger": row["trigger"],
                "account_id": row["account_id"],
                "scheduled_for": row["scheduled_for"],
                "cycle_window_end": row["cycle_window_end"],
                "created_at": row["created_at"],
            }
        )
    snapshots = _build_cycle_snapshots(normalized_rows)
    snapshot_by_cycle_key = {snapshot["cycle_key"]: snapshot for snapshot in snapshots}

    update_rows = [
        {
            "id": row["id"],
            "cycle_key": row["cycle_key"],
            "cycle_expected_accounts": snapshot_by_cycle_key[row["cycle_key"]]["cycle_expected_accounts"],
            "cycle_window_end": snapshot_by_cycle_key[row["cycle_key"]]["cycle_window_end"] or row["scheduled_for"],
        }
        for row in normalized_rows
    ]
    connection.execute(
        sa.text(
            """
            UPDATE automation_runs
            SET
                cycle_key = :cycle_key,
                cycle_expected_accounts = :cycle_expected_accounts,
                cycle_window_end = :cycle_window_end
            WHERE id = :id
            """
        ),
        update_rows,
    )
    return normalized_rows


def _rebuild_cycle_tables(connection: Connection, rows: list[_NormalizedRunRow]) -> None:
    snapshots = _build_cycle_snapshots(rows)
    connection.execute(sa.text("DELETE FROM automation_run_cycle_accounts"))
    connection.execute(sa.text("DELETE FROM automation_run_cycles"))

    for snapshot in snapshots:
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
        for position, (account_id, scheduled_for) in enumerate(snapshot["accounts"]):
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


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "automation_runs"):
        return
    if not _table_exists(bind, "automation_run_cycles") or not _table_exists(bind, "automation_run_cycle_accounts"):
        return

    observed_rows = _load_observed_runs(bind)
    if not observed_rows:
        return
    normalized_rows = _normalize_runs(bind, observed_rows)
    _rebuild_cycle_tables(bind, normalized_rows)


def downgrade() -> None:
    return
