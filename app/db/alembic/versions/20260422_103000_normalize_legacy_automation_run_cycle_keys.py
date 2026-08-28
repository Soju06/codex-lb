"""normalize legacy automation run cycle keys

Revision ID: 20260422_103000_normalize_legacy_automation_run_cycle_keys
Revises: 20260421_130000_merge_automation_and_request_log_heads
Create Date: 2026-04-22
"""

from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha1
from typing import TypedDict

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260422_103000_normalize_legacy_automation_run_cycle_keys"
down_revision = "20260421_130000_merge_automation_and_request_log_heads"
branch_labels = None
depends_on = None

_MAX_LEGACY_SCHEDULE_THRESHOLD_MINUTES = 240


class _ObservedRunRow(TypedDict):
    id: str
    cycle_key: str
    slot_key: str
    job_id: str
    trigger: str
    include_paused_accounts: bool
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
    include_paused_accounts: bool
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
    include_paused_accounts: bool
    created_at: datetime
    accounts: list[tuple[str, datetime]]


class _MutableCycleSnapshot(TypedDict):
    job_id: str
    trigger: str
    cycle_window_end: datetime | None
    include_paused_accounts: bool
    created_at: datetime
    accounts: dict[str, datetime]


class _ObservedExistingCycleSnapshot(TypedDict):
    cycle_key: str
    job_id: str
    trigger: str
    cycle_expected_accounts: int
    cycle_window_end: datetime | None
    include_paused_accounts: bool
    created_at: datetime
    schedule_threshold_minutes: int | None
    accounts: list[tuple[str, datetime]]


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


def _extract_legacy_scheduled_digest(value: str, *, job_id: str) -> str | None:
    if not _looks_like_legacy_scheduled_digest(value, job_id=job_id):
        return None
    return value.rsplit(":", 1)[-1]


def _scheduled_cycle_key(job_id: str, due_slot: datetime) -> str:
    return f"scheduled:{job_id}:{due_slot.isoformat()}"


def _scheduled_slot_digest(job_id: str, *, account_id: str, due_slot: datetime) -> str:
    seed = f"{job_id}:{due_slot.isoformat()}:{account_id}"
    return sha1(seed.encode("utf-8")).hexdigest()[:20]


def _recover_legacy_scheduled_due_slot_from_account(
    *,
    cycle_key: str,
    job_id: str,
    account_id: str,
    scheduled_for: datetime,
    schedule_threshold_minutes: int | None,
) -> tuple[datetime, int] | None:
    digest = _extract_legacy_scheduled_digest(cycle_key, job_id=job_id)
    if digest is None:
        return None

    current_threshold_minutes = max(0, schedule_threshold_minutes or 0)
    search_window_minutes = max(current_threshold_minutes, _MAX_LEGACY_SCHEDULE_THRESHOLD_MINUTES)
    candidate_due_slot = scheduled_for.replace(second=0, microsecond=0)
    for offset_minutes in range(search_window_minutes + 1):
        due_slot = candidate_due_slot - timedelta(minutes=offset_minutes)
        if _scheduled_slot_digest(job_id, account_id=account_id, due_slot=due_slot) == digest:
            return due_slot, offset_minutes
    return None


def _recover_legacy_scheduled_due_slot(row: _ObservedRunRow) -> tuple[datetime, int] | None:
    account_id = row["account_id"]
    if account_id is None:
        return None

    recovered_due_slot = _recover_legacy_scheduled_due_slot_from_account(
        cycle_key=row["cycle_key"],
        job_id=row["job_id"],
        account_id=account_id,
        scheduled_for=row["scheduled_for"],
        schedule_threshold_minutes=row["schedule_threshold_minutes"],
    )
    if recovered_due_slot is not None:
        return recovered_due_slot
    return _recover_legacy_scheduled_due_slot_from_account(
        cycle_key=row["slot_key"],
        job_id=row["job_id"],
        account_id=account_id,
        scheduled_for=row["scheduled_for"],
        schedule_threshold_minutes=row["schedule_threshold_minutes"],
    )


def _normalize_run_row(row: _ObservedRunRow) -> _NormalizedRunRow:
    trigger = row["trigger"]
    cycle_key = row["cycle_key"]
    if trigger == "manual":
        normalized_cycle_key = _normalize_legacy_manual_cycle_key(cycle_key)
        if normalized_cycle_key is not None:
            cycle_key = normalized_cycle_key
        normalized_slot_cycle_key = _normalize_legacy_manual_cycle_key(row["slot_key"])
        if normalized_slot_cycle_key is not None:
            cycle_key = normalized_slot_cycle_key
        return {
            "id": row["id"],
            "cycle_key": cycle_key,
            "job_id": row["job_id"],
            "trigger": row["trigger"],
            "include_paused_accounts": row["include_paused_accounts"],
            "account_id": row["account_id"],
            "scheduled_for": row["scheduled_for"],
            "cycle_window_end": row["cycle_window_end"],
            "created_at": row["created_at"],
        }

    cycle_window_end = row["cycle_window_end"]
    recovered_due_slot = _recover_legacy_scheduled_due_slot(row)
    if trigger == "scheduled" and recovered_due_slot is not None:
        due_slot, recovered_offset_minutes = recovered_due_slot
        threshold_minutes = max(0, row["schedule_threshold_minutes"] or 0)
        cycle_key = _scheduled_cycle_key(row["job_id"], due_slot)
        recovered_window_end = due_slot + timedelta(minutes=max(threshold_minutes, recovered_offset_minutes))
        if cycle_window_end is None or recovered_window_end > cycle_window_end:
            cycle_window_end = recovered_window_end

    return {
        "id": row["id"],
        "cycle_key": cycle_key,
        "job_id": row["job_id"],
        "trigger": row["trigger"],
        "include_paused_accounts": row["include_paused_accounts"],
        "account_id": row["account_id"],
        "scheduled_for": row["scheduled_for"],
        "cycle_window_end": cycle_window_end,
        "created_at": row["created_at"],
    }


def _new_mutable_cycle_snapshot(row: _NormalizedRunRow) -> _MutableCycleSnapshot:
    return {
        "job_id": row["job_id"],
        "trigger": row["trigger"],
        "cycle_window_end": row["cycle_window_end"] or row["scheduled_for"],
        "include_paused_accounts": row["include_paused_accounts"],
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
                "include_paused_accounts": snapshot["include_paused_accounts"],
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
                automation_jobs.include_paused_accounts,
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
                "include_paused_accounts": bool(row["include_paused_accounts"]),
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


def _load_existing_cycle_snapshots(connection: Connection) -> list[_ObservedExistingCycleSnapshot]:
    account_rows = connection.execute(
        sa.text(
            """
            SELECT
                automation_run_cycles.cycle_key,
                automation_run_cycles.job_id,
                automation_run_cycles.trigger,
                automation_run_cycles.cycle_expected_accounts,
                automation_run_cycles.cycle_window_end,
                automation_run_cycles.include_paused_accounts,
                automation_run_cycles.created_at,
                automation_jobs.schedule_threshold_minutes,
                automation_run_cycle_accounts.account_id,
                automation_run_cycle_accounts.scheduled_for
            FROM automation_run_cycles
            JOIN automation_jobs ON automation_jobs.id = automation_run_cycles.job_id
            LEFT JOIN automation_run_cycle_accounts
                ON automation_run_cycle_accounts.cycle_key = automation_run_cycles.cycle_key
            ORDER BY
                automation_run_cycles.created_at ASC,
                automation_run_cycles.cycle_key ASC,
                automation_run_cycle_accounts.position ASC,
                automation_run_cycle_accounts.account_id ASC
            """
        )
    ).mappings()
    snapshots: dict[str, _ObservedExistingCycleSnapshot] = {}
    for row in account_rows:
        created_at = _coerce_datetime(row["created_at"])
        assert created_at is not None
        cycle_key = str(row["cycle_key"])
        snapshot = snapshots.setdefault(
            cycle_key,
            {
                "cycle_key": cycle_key,
                "job_id": str(row["job_id"]),
                "trigger": str(row["trigger"]),
                "cycle_expected_accounts": int(row["cycle_expected_accounts"] or 0),
                "cycle_window_end": _coerce_datetime(row["cycle_window_end"]),
                "include_paused_accounts": bool(row["include_paused_accounts"]),
                "created_at": created_at,
                "schedule_threshold_minutes": (
                    int(row["schedule_threshold_minutes"]) if row["schedule_threshold_minutes"] is not None else None
                ),
                "accounts": [],
            },
        )
        scheduled_for = _coerce_datetime(row["scheduled_for"])
        account_id = row["account_id"]
        if scheduled_for is not None and account_id is not None:
            snapshot["accounts"].append((str(account_id), scheduled_for))
    return list(snapshots.values())


def _normalize_runs(connection: Connection, rows: list[_ObservedRunRow]) -> list[_NormalizedRunRow]:
    normalized_rows: list[_NormalizedRunRow] = []
    for row in rows:
        normalized_rows.append(_normalize_run_row(row))
    return normalized_rows


def _normalize_existing_cycle_snapshot(snapshot: _ObservedExistingCycleSnapshot) -> _ObservedCycleSnapshot:
    cycle_key = snapshot["cycle_key"]
    cycle_window_end = snapshot["cycle_window_end"]
    if snapshot["trigger"] == "manual":
        normalized_cycle_key = _normalize_legacy_manual_cycle_key(cycle_key)
        if normalized_cycle_key is not None:
            cycle_key = normalized_cycle_key
    elif snapshot["trigger"] == "scheduled":
        recovered_slots = [
            recovered
            for account_id, scheduled_for in snapshot["accounts"]
            if (
                recovered := _recover_legacy_scheduled_due_slot_from_account(
                    cycle_key=cycle_key,
                    job_id=snapshot["job_id"],
                    account_id=account_id,
                    scheduled_for=scheduled_for,
                    schedule_threshold_minutes=snapshot["schedule_threshold_minutes"],
                )
            )
            is not None
        ]
        if recovered_slots:
            due_slot = min(recovered_slots, key=lambda item: item[0])[0]
            cycle_key = _scheduled_cycle_key(snapshot["job_id"], due_slot)
            max_offset_minutes = max(offset_minutes for _, offset_minutes in recovered_slots)
            threshold_minutes = max(0, snapshot["schedule_threshold_minutes"] or 0)
            recovered_window_end = due_slot + timedelta(minutes=max(threshold_minutes, max_offset_minutes))
            if cycle_window_end is None or recovered_window_end > cycle_window_end:
                cycle_window_end = recovered_window_end

    return {
        "cycle_key": cycle_key,
        "job_id": snapshot["job_id"],
        "trigger": snapshot["trigger"],
        "cycle_expected_accounts": max(snapshot["cycle_expected_accounts"], len(snapshot["accounts"])),
        "cycle_window_end": cycle_window_end,
        "include_paused_accounts": snapshot["include_paused_accounts"],
        "created_at": snapshot["created_at"],
        "accounts": list(snapshot["accounts"]),
    }


def _rewrite_normalized_runs(
    connection: Connection,
    rows: list[_NormalizedRunRow],
    snapshots: list[_ObservedCycleSnapshot],
) -> None:
    snapshot_by_cycle_key = {snapshot["cycle_key"]: snapshot for snapshot in snapshots}

    update_rows = [
        {
            "id": row["id"],
            "cycle_key": row["cycle_key"],
            "cycle_expected_accounts": snapshot_by_cycle_key[row["cycle_key"]]["cycle_expected_accounts"],
            "cycle_window_end": snapshot_by_cycle_key[row["cycle_key"]]["cycle_window_end"] or row["scheduled_for"],
        }
        for row in rows
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


def _merge_cycle_snapshots(
    normalized_snapshots: list[_ObservedCycleSnapshot],
    existing_snapshots: list[_ObservedExistingCycleSnapshot],
    cycle_key_redirects: dict[str, str],
) -> list[_ObservedCycleSnapshot]:
    merged: dict[str, _MutableCycleSnapshot] = {}
    expected_accounts: dict[str, int] = {}

    normalized_existing_snapshots = [_normalize_existing_cycle_snapshot(snapshot) for snapshot in existing_snapshots]
    for source in [*normalized_existing_snapshots, *normalized_snapshots]:
        cycle_key = cycle_key_redirects.get(source["cycle_key"], source["cycle_key"])
        snapshot = merged.setdefault(
            cycle_key,
            {
                "job_id": source["job_id"],
                "trigger": source["trigger"],
                "cycle_window_end": source["cycle_window_end"],
                "include_paused_accounts": source["include_paused_accounts"],
                "created_at": source["created_at"],
                "accounts": {},
            },
        )
        expected_accounts[cycle_key] = max(
            expected_accounts.get(cycle_key, 0),
            source["cycle_expected_accounts"],
        )
        if source["cycle_window_end"] is not None:
            current_window_end = snapshot["cycle_window_end"]
            if current_window_end is None or source["cycle_window_end"] > current_window_end:
                snapshot["cycle_window_end"] = source["cycle_window_end"]
        if source["created_at"] < snapshot["created_at"]:
            snapshot["created_at"] = source["created_at"]
        for account_id, scheduled_for in source["accounts"]:
            current_scheduled_for = snapshot["accounts"].get(account_id)
            if current_scheduled_for is None or scheduled_for < current_scheduled_for:
                snapshot["accounts"][account_id] = scheduled_for

    result: list[_ObservedCycleSnapshot] = []
    for cycle_key, snapshot in merged.items():
        account_rows = sorted(snapshot["accounts"].items(), key=lambda item: (item[1], item[0]))
        result.append(
            {
                "cycle_key": cycle_key,
                "job_id": snapshot["job_id"],
                "trigger": snapshot["trigger"],
                "cycle_expected_accounts": max(expected_accounts.get(cycle_key, 0), len(account_rows)),
                "cycle_window_end": snapshot["cycle_window_end"],
                "include_paused_accounts": snapshot["include_paused_accounts"],
                "created_at": snapshot["created_at"],
                "accounts": account_rows,
            }
        )
    return sorted(result, key=lambda snapshot: snapshot["cycle_key"])


def _cycle_key_redirects(rows: list[_ObservedRunRow], normalized_rows: list[_NormalizedRunRow]) -> dict[str, str]:
    redirects: dict[str, str] = {}
    for row, normalized in zip(rows, normalized_rows, strict=True):
        if row["cycle_key"] != normalized["cycle_key"]:
            redirects[row["cycle_key"]] = normalized["cycle_key"]
    return redirects


def _rebuild_cycle_tables(
    connection: Connection,
    rows: list[_ObservedRunRow],
    normalized_rows: list[_NormalizedRunRow],
) -> None:
    snapshots = _merge_cycle_snapshots(
        _build_cycle_snapshots(normalized_rows),
        _load_existing_cycle_snapshots(connection),
        _cycle_key_redirects(rows, normalized_rows),
    )
    _rewrite_normalized_runs(connection, normalized_rows, snapshots)
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
                    include_paused_accounts,
                    created_at
                ) VALUES (
                    :cycle_key,
                    :job_id,
                    :trigger,
                    :cycle_expected_accounts,
                    :cycle_window_end,
                    :include_paused_accounts,
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
                "include_paused_accounts": snapshot["include_paused_accounts"],
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
    if "include_paused_accounts" not in _column_names(bind, "automation_run_cycles"):
        op.add_column(
            "automation_run_cycles",
            sa.Column("include_paused_accounts", sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    observed_rows = _load_observed_runs(bind)
    if not observed_rows:
        return
    normalized_rows = _normalize_runs(bind, observed_rows)
    _rebuild_cycle_tables(bind, observed_rows, normalized_rows)


def downgrade() -> None:
    return
