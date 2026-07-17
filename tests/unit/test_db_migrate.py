from __future__ import annotations

import importlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.util.exc import CommandError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy import exc as sa_exc
from sqlalchemy.engine import Connection

import app.db.migrate as migrate_module
from app.db.alembic.revision_ids import OLD_TO_NEW_REVISION_MAP
from app.db.backup import create_sqlite_pre_migration_backup, list_sqlite_pre_migration_backups
from app.db.migrate import (
    MigrationBootstrapError,
    _build_alembic_config,
    _collect_migration_policy_violations,
    _ensure_alembic_version_table_capacity_for_connection,
    _max_revision_id_length,
    _read_current_revisions_from_connection,
    check_migration_policy,
    check_schema_drift,
    inspect_migration_state,
    run_upgrade,
    wait_for_connection,
    wait_for_head,
)
from app.db.migration_url import to_sync_database_url
from app.db.models import Base
from app.modules.usage.additional_quota_keys import clear_additional_quota_registry_cache


def _db_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path}"


def test_check_schema_drift_disposes_sync_engine(monkeypatch) -> None:
    class _FakeConnectionContext:
        def __init__(self) -> None:
            self.connection = object()

        def __enter__(self) -> object:
            return self.connection

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    class _FakeEngine:
        def __init__(self) -> None:
            self.connection_context = _FakeConnectionContext()
            self.disposed = False

        def connect(self) -> _FakeConnectionContext:
            return self.connection_context

        def dispose(self) -> None:
            self.disposed = True

    fake_engine = _FakeEngine()

    monkeypatch.setattr(migrate_module, "create_engine", lambda *args, **kwargs: fake_engine)
    monkeypatch.setattr(
        migrate_module.MigrationContext,
        "configure",
        lambda *, connection, opts: SimpleNamespace(connection=connection, opts=opts),
    )
    monkeypatch.setattr(migrate_module, "compare_metadata", lambda context, metadata: [])
    monkeypatch.setattr(migrate_module, "_manual_schema_drift_diffs", lambda connection: ())

    assert check_schema_drift("sqlite+aiosqlite:///tmp/drift.db") == ()
    assert fake_engine.disposed is True


def test_inspect_migration_state_requires_upgrade_when_uninitialized(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.db"
    state = inspect_migration_state(_db_url(db_path))

    assert state.needs_upgrade is True
    assert state.current_revision is None
    assert state.has_alembic_version_table is False


def test_inspect_migration_state_no_upgrade_after_head(tmp_path: Path) -> None:
    db_path = tmp_path / "head.db"
    url = _db_url(db_path)

    result = run_upgrade(url, "head", bootstrap_legacy=False)
    state = inspect_migration_state(url)

    assert result.current_revision == state.head_revision
    assert state.needs_upgrade is False
    assert state.current_revision == state.head_revision
    assert state.has_alembic_version_table is True


def test_wait_for_head_returns_once_schema_is_current(monkeypatch) -> None:
    states = iter(
        [
            SimpleNamespace(
                current_revision=None,
                head_revision="head",
                has_alembic_version_table=False,
                has_legacy_migrations_table=False,
                needs_upgrade=True,
            ),
            SimpleNamespace(
                current_revision="head",
                head_revision="head",
                has_alembic_version_table=True,
                has_legacy_migrations_table=False,
                needs_upgrade=False,
            ),
        ]
    )
    sleep_calls: list[float] = []

    monkeypatch.setattr(migrate_module, "inspect_migration_state", lambda _url: next(states))
    monkeypatch.setattr(migrate_module.time, "sleep", lambda seconds: sleep_calls.append(seconds))
    monotonic_values = iter([0.0, 0.5])
    monkeypatch.setattr(migrate_module.time, "monotonic", lambda: next(monotonic_values, 0.5))

    state = wait_for_head("sqlite+aiosqlite:///tmp/test.db", timeout_seconds=5.0, interval_seconds=1.0)

    assert state.current_revision == "head"
    assert sleep_calls == [1.0]


def test_wait_for_head_times_out_when_schema_never_reaches_head(monkeypatch) -> None:
    monkeypatch.setattr(
        migrate_module,
        "inspect_migration_state",
        lambda _url: SimpleNamespace(
            current_revision=None,
            head_revision="head",
            has_alembic_version_table=False,
            has_legacy_migrations_table=False,
            needs_upgrade=True,
        ),
    )
    monkeypatch.setattr(migrate_module.time, "sleep", lambda _seconds: None)
    monotonic_values = iter([0.0, 1.0, 2.1])
    monkeypatch.setattr(migrate_module.time, "monotonic", lambda: next(monotonic_values, 2.1))

    with pytest.raises(TimeoutError, match="Timed out waiting for database schema to reach Alembic head"):
        wait_for_head("sqlite+aiosqlite:///tmp/test.db", timeout_seconds=2.0, interval_seconds=1.0)


def test_wait_for_connection_returns_once_database_is_reachable(monkeypatch) -> None:
    attempts = {"count": 0}
    sleep_calls: list[float] = []

    class _FakeContext:
        def __enter__(self) -> None:
            return None

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    def _sync_connection(_: str):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("db not ready")
        return _FakeContext()

    monkeypatch.setattr(migrate_module, "_sync_connection", _sync_connection)
    monkeypatch.setattr(migrate_module.time, "sleep", lambda seconds: sleep_calls.append(seconds))
    monotonic_values = iter([0.0, 0.5])
    monkeypatch.setattr(migrate_module.time, "monotonic", lambda: next(monotonic_values, 0.5))

    wait_for_connection("sqlite+aiosqlite:///tmp/test.db", timeout_seconds=5.0, interval_seconds=1.0)

    assert attempts["count"] == 2
    assert sleep_calls == [1.0]


def test_wait_for_connection_times_out_when_database_stays_unreachable(monkeypatch) -> None:
    monkeypatch.setattr(migrate_module, "_sync_connection", lambda _: (_ for _ in ()).throw(RuntimeError("db down")))
    monkeypatch.setattr(migrate_module.time, "sleep", lambda _seconds: None)
    monotonic_values = iter([0.0, 1.0, 2.1])
    monkeypatch.setattr(migrate_module.time, "monotonic", lambda: next(monotonic_values, 2.1))

    with pytest.raises(TimeoutError, match="Timed out waiting for database connectivity"):
        wait_for_connection("sqlite+aiosqlite:///tmp/test.db", timeout_seconds=2.0, interval_seconds=1.0)


def test_schema_migration_contract_matches_after_upgrade(tmp_path: Path) -> None:
    """Prisma-style contract: migrated schema must match ORM metadata and policy."""
    db_path = tmp_path / "contract.db"
    url = _db_url(db_path)

    run_upgrade(url, "head", bootstrap_legacy=False)

    assert check_migration_policy(url) == ()
    assert check_schema_drift(url) == ()


def test_accounts_codex_installation_id_migration_backfills_existing_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "codex-installation-id.db"
    url = _db_url(db_path)
    parent_revision = "20260607_000000_merge_weekly_monthly_useragent_heads"
    target_revision = "20260613_000000_add_accounts_codex_installation_id"

    run_upgrade(url, parent_revision, bootstrap_legacy=False)
    engine = create_engine(to_sync_database_url(url), future=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO accounts "
                    "(id, email, plan_type, access_token_encrypted, refresh_token_encrypted, "
                    "id_token_encrypted, last_refresh, status, deactivation_reason) "
                    "VALUES (:id, :email, :plan_type, :access_token, :refresh_token, "
                    ":id_token, :last_refresh, :status, :deactivation_reason)"
                ),
                {
                    "id": "acc_codex_installation_backfill",
                    "email": "codex-installation@example.com",
                    "plan_type": "plus",
                    "access_token": b"access",
                    "refresh_token": b"refresh",
                    "id_token": b"id",
                    "last_refresh": "2026-06-10 00:00:00",
                    "status": "active",
                    "deactivation_reason": None,
                },
            )
    finally:
        engine.dispose()

    run_upgrade(url, target_revision, bootstrap_legacy=False)

    engine = create_engine(to_sync_database_url(url), future=True)
    try:
        with engine.begin() as connection:
            columns = {column["name"]: column for column in inspect(connection).get_columns("accounts")}
            value = connection.execute(
                text("SELECT codex_installation_id FROM accounts WHERE id = :id"),
                {"id": "acc_codex_installation_backfill"},
            ).scalar_one()
    finally:
        engine.dispose()

    assert columns["codex_installation_id"]["nullable"] is False
    assert isinstance(value, str)
    assert len(value) == 36


def test_reauth_required_status_migration_downgrade_remaps_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "reauth-status.db"
    url = _db_url(db_path)
    parent_revision = "20260602_080000_merge_upstream_proxy_and_quota_planner_heads"
    reauth_revision = "20260604_000000_add_reauth_required_account_status"

    run_upgrade(url, parent_revision, bootstrap_legacy=False)
    engine = create_engine(to_sync_database_url(url))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO accounts "
                    "(id, email, plan_type, access_token_encrypted, refresh_token_encrypted, "
                    "id_token_encrypted, last_refresh, status, deactivation_reason) "
                    "VALUES (:id, :email, :plan_type, :access_token, :refresh_token, "
                    ":id_token, :last_refresh, :status, :deactivation_reason)"
                ),
                [
                    {
                        "id": "acc_reauth_downgrade",
                        "email": "reauth-downgrade@example.com",
                        "plan_type": "plus",
                        "access_token": b"access",
                        "refresh_token": b"refresh",
                        "id_token": b"id",
                        "last_refresh": "2026-06-04 00:00:00",
                        "status": "deactivated",
                        "deactivation_reason": "Authentication token invalidated - re-login required",
                    },
                    {
                        "id": "acc_usage_reauth_downgrade",
                        "email": "usage-reauth-downgrade@example.com",
                        "plan_type": "plus",
                        "access_token": b"access",
                        "refresh_token": b"refresh",
                        "id_token": b"id",
                        "last_refresh": "2026-06-04 00:00:00",
                        "status": "deactivated",
                        "deactivation_reason": (
                            "Usage API error: HTTP 401 - Your authentication token has been invalidated. "
                            "Please try signing in again."
                        ),
                    },
                    {
                        "id": "acc_disabled_downgrade",
                        "email": "disabled-downgrade@example.com",
                        "plan_type": "plus",
                        "access_token": b"access",
                        "refresh_token": b"refresh",
                        "id_token": b"id",
                        "last_refresh": "2026-06-04 00:00:00",
                        "status": "deactivated",
                        "deactivation_reason": "Usage API error: HTTP 401 - Account has been deactivated",
                    },
                ],
            )
    finally:
        engine.dispose()

    run_upgrade(url, reauth_revision, bootstrap_legacy=False)

    engine = create_engine(to_sync_database_url(url))
    try:
        with engine.connect() as connection:
            status_rows = connection.execute(
                text("SELECT id, status FROM accounts WHERE id IN (:exact_id, :usage_id, :disabled_id) ORDER BY id"),
                {
                    "exact_id": "acc_reauth_downgrade",
                    "usage_id": "acc_usage_reauth_downgrade",
                    "disabled_id": "acc_disabled_downgrade",
                },
            )
            statuses = {str(row[0]): str(row[1]) for row in status_rows}
            status_column = next(
                column for column in inspect(connection).get_columns("accounts") if column["name"] == "status"
            )

        assert statuses == {
            "acc_disabled_downgrade": "deactivated",
            "acc_reauth_downgrade": "reauth_required",
            "acc_usage_reauth_downgrade": "reauth_required",
        }
        assert str(status_column["type"]).upper() == "VARCHAR(15)"

        command.downgrade(_build_alembic_config(url), parent_revision)

        with engine.connect() as connection:
            status_rows = connection.execute(
                text("SELECT id, status FROM accounts WHERE id IN (:exact_id, :usage_id, :disabled_id) ORDER BY id"),
                {
                    "exact_id": "acc_reauth_downgrade",
                    "usage_id": "acc_usage_reauth_downgrade",
                    "disabled_id": "acc_disabled_downgrade",
                },
            )
            statuses = {str(row[0]): str(row[1]) for row in status_rows}
            status_column = next(
                column for column in inspect(connection).get_columns("accounts") if column["name"] == "status"
            )
    finally:
        engine.dispose()

    assert statuses == {
        "acc_disabled_downgrade": "deactivated",
        "acc_reauth_downgrade": "deactivated",
        "acc_usage_reauth_downgrade": "deactivated",
    }
    assert str(status_column["type"]).upper() == "VARCHAR(14)"
    assert inspect_migration_state(url).current_revision == parent_revision


def test_http_downstream_transport_policy_migration_round_trips_with_default_null_api_key(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "http-transport-policy.db"
    url = _db_url(db_path)
    parent_revision = "20260611_000000_merge_dashboard_guest_and_weekly_useragent_heads"
    revision = "20260626_000000_add_http_downstream_transport_policy"

    run_upgrade(url, parent_revision, bootstrap_legacy=False)
    engine = create_engine(to_sync_database_url(url))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO api_keys "
                    "(id, name, key_hash, key_prefix, is_active, created_at) "
                    "VALUES (:id, :name, :key_hash, :key_prefix, :is_active, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": "key_transport_policy_migration",
                    "name": "transport policy migration",
                    "key_hash": "hash_transport_policy_migration",
                    "key_prefix": "sk-migration",
                    "is_active": True,
                },
            )

        config = _build_alembic_config(url)
        command.upgrade(config, revision)
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT transport_policy_override FROM api_keys WHERE id = 'key_transport_policy_migration'")
                ).scalar_one()
                is None
            )
            inspector = inspect(connection)
            assert "transport_policy_override" in {column["name"] for column in inspector.get_columns("api_keys")}
            assert "http_downstream_transport_policy" in {
                column["name"] for column in inspector.get_columns("dashboard_settings")
            }

        command.downgrade(config, parent_revision)
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert "transport_policy_override" not in {column["name"] for column in inspector.get_columns("api_keys")}
            assert "http_downstream_transport_policy" not in {
                column["name"] for column in inspector.get_columns("dashboard_settings")
            }
    finally:
        engine.dispose()


def test_base_revision_does_not_depend_on_live_metadata(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "base.db"
    url = _db_url(db_path)

    def _raise_create_all(*_: object, **__: object) -> None:
        raise AssertionError("base revision must not call Base.metadata.create_all")

    monkeypatch.setattr(Base.metadata, "create_all", _raise_create_all)

    base_revision = OLD_TO_NEW_REVISION_MAP["000_base_schema"]
    result = run_upgrade(url, base_revision, bootstrap_legacy=False)
    assert result.current_revision == base_revision


def test_request_logs_transport_stays_in_additive_migration_chain(tmp_path: Path) -> None:
    db_path = tmp_path / "request-logs-transport.db"
    url = _db_url(db_path)
    base_revision = OLD_TO_NEW_REVISION_MAP["000_base_schema"]
    transport_revision = "20260310_000000_add_request_logs_transport"

    run_upgrade(url, base_revision, bootstrap_legacy=False)

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).connect() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("request_logs")}
        assert "transport" in columns

    result = run_upgrade(url, transport_revision, bootstrap_legacy=False)
    assert result.current_revision == transport_revision

    with create_engine(sync_url, future=True).connect() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("request_logs")}
        assert "transport" in columns


def test_request_logs_response_lookup_migration_handles_preexisting_session_id_column(tmp_path: Path) -> None:
    db_path = tmp_path / "request-logs-session-id-drift.db"
    url = _db_url(db_path)
    pre_revision = "20260413_000000_add_accounts_blocked_at"
    target_revision = "20260415_160000_add_request_logs_response_lookup_index"

    run_upgrade(url, pre_revision, bootstrap_legacy=False)

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).connect() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("request_logs")}
        assert "session_id" not in columns
        connection.execute(text("ALTER TABLE request_logs ADD COLUMN session_id VARCHAR"))
        connection.commit()

    result = run_upgrade(url, target_revision, bootstrap_legacy=False)
    assert result.current_revision == target_revision

    with create_engine(sync_url, future=True).connect() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("request_logs")}
        assert "session_id" in columns
        index_names = {index["name"] for index in inspect(connection).get_indexes("request_logs")}
        assert "idx_logs_request_status_api_key_time" in index_names
        assert "idx_logs_request_status_api_key_session_time" in index_names


def test_quota_planner_migration_repairs_preexisting_request_kind_column(tmp_path: Path) -> None:
    db_path = tmp_path / "quota-planner-request-kind-drift.db"
    url = _db_url(db_path)
    pre_revision = "20260520_000000_merge_api_key_and_http_bridge_heads"
    target_revision = "20260520_030000_add_quota_planner"

    run_upgrade(url, pre_revision, bootstrap_legacy=False)

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).begin() as connection:
        connection.execute(text("ALTER TABLE request_logs ADD COLUMN source VARCHAR"))
        connection.execute(text("ALTER TABLE request_logs ADD COLUMN request_kind VARCHAR"))
        connection.execute(
            text(
                """
                INSERT INTO request_logs (
                    id,
                    request_id,
                    model,
                    status,
                    source,
                    request_kind
                ) VALUES
                    (1, 'normal-null', 'gpt-5.4-mini', 'success', NULL, NULL),
                    (2, 'normal-empty', 'gpt-5.4-mini', 'success', NULL, ''),
                    (3, 'warmup-null', 'gpt-5.4-mini', 'success', 'limit_warmup', NULL)
                """
            )
        )

    result = run_upgrade(url, target_revision, bootstrap_legacy=False)
    assert result.current_revision == target_revision

    with create_engine(sync_url, future=True).connect() as connection:
        rows = connection.execute(text("SELECT request_id, request_kind FROM request_logs ORDER BY id")).fetchall()
        request_kind_column = next(
            column for column in inspect(connection).get_columns("request_logs") if column["name"] == "request_kind"
        )

    assert rows == [
        ("normal-null", "normal"),
        ("normal-empty", "normal"),
        ("warmup-null", "warmup"),
    ]
    assert request_kind_column["nullable"] is False
    assert request_kind_column["default"] is not None


def test_automation_run_cycle_snapshot_migration_normalizes_legacy_manual_keys(tmp_path: Path) -> None:
    db_path = tmp_path / "automation-cycle-snapshots.db"
    url = _db_url(db_path)
    pre_revision = "20260419_000000_add_automation_run_cycle_metadata"
    target_revision = "20260419_020000_add_automation_run_cycles_snapshot_tables"

    run_upgrade(url, pre_revision, bootstrap_legacy=False)

    sync_url = to_sync_database_url(url)
    created_at = datetime(2026, 4, 19, 3, 0, 0)
    with create_engine(sync_url, future=True).begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO accounts (
                    id,
                    email,
                    plan_type,
                    access_token_encrypted,
                    refresh_token_encrypted,
                    id_token_encrypted,
                    last_refresh,
                    status,
                    deactivation_reason,
                    chatgpt_account_id,
                    reset_at
                ) VALUES (
                    :id,
                    :email,
                    :plan_type,
                    :access_token_encrypted,
                    :refresh_token_encrypted,
                    :id_token_encrypted,
                    :last_refresh,
                    :status,
                    :deactivation_reason,
                    :chatgpt_account_id,
                    :reset_at
                )
                """
            ),
            [
                {
                    "id": "acc_snapshot_a",
                    "email": "snapshot-a@example.com",
                    "plan_type": "plus",
                    "access_token_encrypted": b"access-a",
                    "refresh_token_encrypted": b"refresh-a",
                    "id_token_encrypted": b"id-a",
                    "last_refresh": created_at,
                    "status": "active",
                    "deactivation_reason": None,
                    "chatgpt_account_id": None,
                    "reset_at": None,
                },
                {
                    "id": "acc_snapshot_b",
                    "email": "snapshot-b@example.com",
                    "plan_type": "plus",
                    "access_token_encrypted": b"access-b",
                    "refresh_token_encrypted": b"refresh-b",
                    "id_token_encrypted": b"id-b",
                    "last_refresh": created_at,
                    "status": "active",
                    "deactivation_reason": None,
                    "chatgpt_account_id": None,
                    "reset_at": None,
                },
            ],
        )
        connection.execute(
            text(
                """
                INSERT INTO automation_jobs (
                    id,
                    name,
                    enabled,
                    schedule_type,
                    schedule_time,
                    schedule_timezone,
                    schedule_days,
                    schedule_threshold_minutes,
                    include_paused_accounts,
                    model,
                    reasoning_effort,
                    prompt,
                    created_at,
                    updated_at
                ) VALUES (
                    :id,
                    :name,
                    :enabled,
                    :schedule_type,
                    :schedule_time,
                    :schedule_timezone,
                    :schedule_days,
                    :schedule_threshold_minutes,
                    :include_paused_accounts,
                    :model,
                    :reasoning_effort,
                    :prompt,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": "job_snapshot",
                "name": "Snapshot job",
                "enabled": True,
                "schedule_type": "daily",
                "schedule_time": "05:00",
                "schedule_timezone": "UTC",
                "schedule_days": "mon,tue,wed,thu,fri,sat,sun",
                "schedule_threshold_minutes": 5,
                "include_paused_accounts": False,
                "model": "gpt-5.4-mini",
                "reasoning_effort": None,
                "prompt": "ping",
                "created_at": created_at,
                "updated_at": created_at,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO automation_runs (
                    id,
                    job_id,
                    trigger,
                    slot_key,
                    cycle_key,
                    cycle_expected_accounts,
                    cycle_window_end,
                    scheduled_for,
                    started_at,
                    finished_at,
                    status,
                    account_id,
                    error_code,
                    error_message,
                    attempt_count,
                    created_at
                ) VALUES (
                    :id,
                    :job_id,
                    :trigger,
                    :slot_key,
                    :cycle_key,
                    :cycle_expected_accounts,
                    :cycle_window_end,
                    :scheduled_for,
                    :started_at,
                    :finished_at,
                    :status,
                    :account_id,
                    :error_code,
                    :error_message,
                    :attempt_count,
                    :created_at
                )
                """
            ),
            [
                {
                    "id": "run_snapshot_a",
                    "job_id": "job_snapshot",
                    "trigger": "manual",
                    "slot_key": "manual:job_snapshot:cycle-1:digest-a",
                    "cycle_key": "manual:job_snapshot:cycle-1:digest-a",
                    "cycle_expected_accounts": 0,
                    "cycle_window_end": created_at + timedelta(minutes=5),
                    "scheduled_for": created_at,
                    "started_at": created_at,
                    "finished_at": created_at + timedelta(seconds=5),
                    "status": "success",
                    "account_id": "acc_snapshot_a",
                    "error_code": None,
                    "error_message": None,
                    "attempt_count": 0,
                    "created_at": created_at,
                },
                {
                    "id": "run_snapshot_b",
                    "job_id": "job_snapshot",
                    "trigger": "manual",
                    "slot_key": "manual:job_snapshot:cycle-1:digest-b",
                    "cycle_key": "manual:job_snapshot:cycle-1:digest-b",
                    "cycle_expected_accounts": 99,
                    "cycle_window_end": created_at + timedelta(minutes=5),
                    "scheduled_for": created_at + timedelta(minutes=1),
                    "started_at": created_at + timedelta(minutes=1),
                    "finished_at": created_at + timedelta(minutes=1, seconds=5),
                    "status": "success",
                    "account_id": "acc_snapshot_b",
                    "error_code": None,
                    "error_message": None,
                    "attempt_count": 0,
                    "created_at": created_at + timedelta(minutes=1),
                },
            ],
        )

    result = run_upgrade(url, target_revision, bootstrap_legacy=False)
    assert result.current_revision == target_revision

    with create_engine(sync_url, future=True).connect() as connection:
        cycle_rows = connection.execute(
            text(
                """
                SELECT cycle_key, job_id, trigger, cycle_expected_accounts
                FROM automation_run_cycles
                ORDER BY cycle_key
                """
            )
        ).all()
        assert cycle_rows == [("manual:job_snapshot:cycle-1", "job_snapshot", "manual", 2)]

        account_rows = connection.execute(
            text(
                """
                SELECT cycle_key, account_id, position, scheduled_for
                FROM automation_run_cycle_accounts
                ORDER BY cycle_key, position
                """
            )
        ).all()
        normalized_account_rows = [
            (
                cycle_key,
                account_id,
                position,
                datetime.fromisoformat(scheduled_for) if isinstance(scheduled_for, str) else scheduled_for,
            )
            for cycle_key, account_id, position, scheduled_for in account_rows
        ]
        assert normalized_account_rows == [
            ("manual:job_snapshot:cycle-1", "acc_snapshot_a", 0, created_at),
            ("manual:job_snapshot:cycle-1", "acc_snapshot_b", 1, created_at + timedelta(minutes=1)),
        ]


def test_automation_run_cycle_repair_migration_normalizes_legacy_cycle_keys_for_existing_head(tmp_path: Path) -> None:
    db_path = tmp_path / "automation-cycle-key-repair.db"
    url = _db_url(db_path)
    pre_revision = "20260421_130000_merge_automation_and_request_log_heads"
    target_revision = "20260422_103000_normalize_legacy_automation_run_cycle_keys"

    run_upgrade(url, pre_revision, bootstrap_legacy=False)

    sync_url = to_sync_database_url(url)
    created_at = datetime(2026, 4, 22, 3, 0, 0)
    scheduled_due_slot = created_at + timedelta(hours=1)
    scheduled_window_end = scheduled_due_slot + timedelta(minutes=60)
    scheduled_digest_a = "b32d81b774f1e6d05dfe"
    scheduled_digest_b = "53c9867d6d99b6ffc226"
    with create_engine(sync_url, future=True).begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO accounts (
                    id,
                    email,
                    plan_type,
                    access_token_encrypted,
                    refresh_token_encrypted,
                    id_token_encrypted,
                    last_refresh,
                    status,
                    deactivation_reason,
                    chatgpt_account_id,
                    reset_at
                ) VALUES (
                    :id,
                    :email,
                    :plan_type,
                    :access_token_encrypted,
                    :refresh_token_encrypted,
                    :id_token_encrypted,
                    :last_refresh,
                    :status,
                    :deactivation_reason,
                    :chatgpt_account_id,
                    :reset_at
                )
                """
            ),
            [
                {
                    "id": "acc_cycle_manual_a",
                    "email": "manual-a@example.com",
                    "plan_type": "plus",
                    "access_token_encrypted": b"access-a",
                    "refresh_token_encrypted": b"refresh-a",
                    "id_token_encrypted": b"id-a",
                    "last_refresh": created_at,
                    "status": "active",
                    "deactivation_reason": None,
                    "chatgpt_account_id": None,
                    "reset_at": None,
                },
                {
                    "id": "acc_cycle_manual_b",
                    "email": "manual-b@example.com",
                    "plan_type": "plus",
                    "access_token_encrypted": b"access-b",
                    "refresh_token_encrypted": b"refresh-b",
                    "id_token_encrypted": b"id-b",
                    "last_refresh": created_at,
                    "status": "active",
                    "deactivation_reason": None,
                    "chatgpt_account_id": None,
                    "reset_at": None,
                },
                {
                    "id": "acc_cycle_scheduled_a",
                    "email": "scheduled-a@example.com",
                    "plan_type": "plus",
                    "access_token_encrypted": b"access-c",
                    "refresh_token_encrypted": b"refresh-c",
                    "id_token_encrypted": b"id-c",
                    "last_refresh": created_at,
                    "status": "active",
                    "deactivation_reason": None,
                    "chatgpt_account_id": None,
                    "reset_at": None,
                },
                {
                    "id": "acc_cycle_scheduled_b",
                    "email": "scheduled-b@example.com",
                    "plan_type": "plus",
                    "access_token_encrypted": b"access-d",
                    "refresh_token_encrypted": b"refresh-d",
                    "id_token_encrypted": b"id-d",
                    "last_refresh": created_at,
                    "status": "active",
                    "deactivation_reason": None,
                    "chatgpt_account_id": None,
                    "reset_at": None,
                },
                {
                    "id": "acc_cycle_scheduled_c",
                    "email": "scheduled-c@example.com",
                    "plan_type": "plus",
                    "access_token_encrypted": b"access-e",
                    "refresh_token_encrypted": b"refresh-e",
                    "id_token_encrypted": b"id-e",
                    "last_refresh": created_at,
                    "status": "active",
                    "deactivation_reason": None,
                    "chatgpt_account_id": None,
                    "reset_at": None,
                },
            ],
        )
        connection.execute(
            text(
                """
                INSERT INTO automation_jobs (
                    id,
                    name,
                    enabled,
                    schedule_type,
                    schedule_time,
                    schedule_timezone,
                    schedule_days,
                    schedule_threshold_minutes,
                    include_paused_accounts,
                    model,
                    reasoning_effort,
                    prompt,
                    created_at,
                    updated_at
                ) VALUES (
                    :id,
                    :name,
                    :enabled,
                    :schedule_type,
                    :schedule_time,
                    :schedule_timezone,
                    :schedule_days,
                    :schedule_threshold_minutes,
                    :include_paused_accounts,
                    :model,
                    :reasoning_effort,
                    :prompt,
                    :created_at,
                    :updated_at
                )
                """
            ),
            [
                {
                    "id": "job_cycle_manual",
                    "name": "Manual legacy job",
                    "enabled": True,
                    "schedule_type": "daily",
                    "schedule_time": "05:00",
                    "schedule_timezone": "UTC",
                    "schedule_days": "mon,tue,wed,thu,fri,sat,sun",
                    "schedule_threshold_minutes": 0,
                    "include_paused_accounts": False,
                    "model": "gpt-5.4-mini",
                    "reasoning_effort": None,
                    "prompt": "ping",
                    "created_at": created_at,
                    "updated_at": created_at,
                },
                {
                    "id": "job_cycle_scheduled",
                    "name": "Scheduled legacy job",
                    "enabled": True,
                    "schedule_type": "daily",
                    "schedule_time": "05:00",
                    "schedule_timezone": "UTC",
                    "schedule_days": "mon,tue,wed,thu,fri,sat,sun",
                    "schedule_threshold_minutes": 5,
                    "include_paused_accounts": False,
                    "model": "gpt-5.4-mini",
                    "reasoning_effort": None,
                    "prompt": "ping",
                    "created_at": created_at,
                    "updated_at": created_at,
                },
            ],
        )
        connection.execute(
            text(
                """
                INSERT INTO automation_runs (
                    id,
                    job_id,
                    trigger,
                    slot_key,
                    cycle_key,
                    cycle_expected_accounts,
                    cycle_window_end,
                    scheduled_for,
                    started_at,
                    finished_at,
                    status,
                    account_id,
                    error_code,
                    error_message,
                    attempt_count,
                    created_at
                ) VALUES (
                    :id,
                    :job_id,
                    :trigger,
                    :slot_key,
                    :cycle_key,
                    :cycle_expected_accounts,
                    :cycle_window_end,
                    :scheduled_for,
                    :started_at,
                    :finished_at,
                    :status,
                    :account_id,
                    :error_code,
                    :error_message,
                    :attempt_count,
                    :created_at
                )
                """
            ),
            [
                {
                    "id": "run_manual_repair_a",
                    "job_id": "job_cycle_manual",
                    "trigger": "manual",
                    "slot_key": "manual:job_cycle_manual:cycle-1:digest-a",
                    "cycle_key": "manual:job_cycle_manual:cycle-1:digest-a",
                    "cycle_expected_accounts": 99,
                    "cycle_window_end": created_at + timedelta(minutes=5),
                    "scheduled_for": created_at,
                    "started_at": created_at,
                    "finished_at": created_at + timedelta(seconds=5),
                    "status": "success",
                    "account_id": "acc_cycle_manual_a",
                    "error_code": None,
                    "error_message": None,
                    "attempt_count": 1,
                    "created_at": created_at,
                },
                {
                    "id": "run_manual_repair_b",
                    "job_id": "job_cycle_manual",
                    "trigger": "manual",
                    "slot_key": "manual:job_cycle_manual:cycle-1:digest-b",
                    "cycle_key": "manual:job_cycle_manual:cycle-1:digest-b",
                    "cycle_expected_accounts": 99,
                    "cycle_window_end": created_at + timedelta(minutes=5),
                    "scheduled_for": created_at + timedelta(minutes=1),
                    "started_at": created_at + timedelta(minutes=1),
                    "finished_at": created_at + timedelta(minutes=1, seconds=5),
                    "status": "failed",
                    "account_id": "acc_cycle_manual_b",
                    "error_code": "boom",
                    "error_message": "boom",
                    "attempt_count": 1,
                    "created_at": created_at + timedelta(minutes=1),
                },
                {
                    "id": "run_scheduled_repair_a",
                    "job_id": "job_cycle_scheduled",
                    "trigger": "scheduled",
                    "slot_key": f"scheduled:job_cycle_scheduled:{scheduled_digest_a}",
                    "cycle_key": f"scheduled:job_cycle_scheduled:{scheduled_digest_a}",
                    "cycle_expected_accounts": 2,
                    "cycle_window_end": scheduled_due_slot,
                    "scheduled_for": scheduled_due_slot,
                    "started_at": scheduled_due_slot,
                    "finished_at": scheduled_due_slot + timedelta(seconds=5),
                    "status": "success",
                    "account_id": "acc_cycle_scheduled_a",
                    "error_code": None,
                    "error_message": None,
                    "attempt_count": 1,
                    "created_at": scheduled_due_slot,
                },
                {
                    "id": "run_scheduled_repair_b",
                    "job_id": "job_cycle_scheduled",
                    "trigger": "scheduled",
                    "slot_key": f"scheduled:job_cycle_scheduled:{scheduled_digest_b}",
                    "cycle_key": f"scheduled:job_cycle_scheduled:{scheduled_digest_b}",
                    "cycle_expected_accounts": 2,
                    "cycle_window_end": scheduled_due_slot + timedelta(minutes=60),
                    "scheduled_for": scheduled_due_slot + timedelta(minutes=60),
                    "started_at": scheduled_due_slot + timedelta(minutes=60),
                    "finished_at": scheduled_due_slot + timedelta(minutes=60, seconds=5),
                    "status": "success",
                    "account_id": "acc_cycle_scheduled_b",
                    "error_code": None,
                    "error_message": None,
                    "attempt_count": 1,
                    "created_at": scheduled_due_slot + timedelta(minutes=60),
                },
            ],
        )
        connection.execute(
            text(
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
            [
                {
                    "cycle_key": f"scheduled:job_cycle_scheduled:{scheduled_digest_a}",
                    "job_id": "job_cycle_scheduled",
                    "trigger": "scheduled",
                    "cycle_expected_accounts": 1,
                    "cycle_window_end": scheduled_window_end,
                    "created_at": scheduled_due_slot,
                },
                {
                    "cycle_key": f"scheduled:job_cycle_scheduled:{scheduled_digest_b}",
                    "job_id": "job_cycle_scheduled",
                    "trigger": "scheduled",
                    "cycle_expected_accounts": 1,
                    "cycle_window_end": scheduled_window_end,
                    "created_at": scheduled_due_slot + timedelta(minutes=60),
                },
            ],
        )
        connection.execute(
            text(
                """
                INSERT INTO automation_run_cycle_accounts (
                    cycle_key,
                    account_id,
                    position,
                    scheduled_for,
                    created_at
                ) VALUES (
                    :cycle_key,
                    :account_id,
                    :position,
                    :scheduled_for,
                    :created_at
                )
                """
            ),
            [
                {
                    "cycle_key": f"scheduled:job_cycle_scheduled:{scheduled_digest_a}",
                    "account_id": "acc_cycle_scheduled_a",
                    "position": 0,
                    "scheduled_for": scheduled_due_slot,
                    "created_at": scheduled_due_slot,
                },
                {
                    "cycle_key": f"scheduled:job_cycle_scheduled:{scheduled_digest_a}",
                    "account_id": "acc_cycle_scheduled_c",
                    "position": 1,
                    "scheduled_for": scheduled_due_slot + timedelta(minutes=3),
                    "created_at": scheduled_due_slot,
                },
                {
                    "cycle_key": f"scheduled:job_cycle_scheduled:{scheduled_digest_b}",
                    "account_id": "acc_cycle_scheduled_b",
                    "position": 0,
                    "scheduled_for": scheduled_due_slot + timedelta(minutes=60),
                    "created_at": scheduled_due_slot + timedelta(minutes=60),
                },
            ],
        )

    result = run_upgrade(url, target_revision, bootstrap_legacy=False)
    assert result.current_revision == target_revision

    with create_engine(sync_url, future=True).connect() as connection:
        run_rows = connection.execute(
            text(
                """
                SELECT id, cycle_key, cycle_expected_accounts, cycle_window_end
                FROM automation_runs
                ORDER BY id
                """
            )
        ).all()
        run_rows = [
            (
                run_id,
                cycle_key,
                cycle_expected_accounts,
                datetime.fromisoformat(cycle_window_end) if isinstance(cycle_window_end, str) else cycle_window_end,
            )
            for run_id, cycle_key, cycle_expected_accounts, cycle_window_end in run_rows
        ]
        assert run_rows == [
            ("run_manual_repair_a", "manual:job_cycle_manual:cycle-1", 2, created_at + timedelta(minutes=5)),
            ("run_manual_repair_b", "manual:job_cycle_manual:cycle-1", 2, created_at + timedelta(minutes=5)),
            (
                "run_scheduled_repair_a",
                f"scheduled:job_cycle_scheduled:{scheduled_due_slot.isoformat()}",
                3,
                scheduled_window_end,
            ),
            (
                "run_scheduled_repair_b",
                f"scheduled:job_cycle_scheduled:{scheduled_due_slot.isoformat()}",
                3,
                scheduled_window_end,
            ),
        ]

        cycle_rows = connection.execute(
            text(
                """
                SELECT cycle_key, job_id, trigger, cycle_expected_accounts, cycle_window_end
                FROM automation_run_cycles
                ORDER BY cycle_key
                """
            )
        ).all()
        cycle_rows = [
            (
                cycle_key,
                job_id,
                trigger,
                cycle_expected_accounts,
                datetime.fromisoformat(cycle_window_end) if isinstance(cycle_window_end, str) else cycle_window_end,
            )
            for cycle_key, job_id, trigger, cycle_expected_accounts, cycle_window_end in cycle_rows
        ]
        assert cycle_rows == [
            ("manual:job_cycle_manual:cycle-1", "job_cycle_manual", "manual", 2, created_at + timedelta(minutes=5)),
            (
                f"scheduled:job_cycle_scheduled:{scheduled_due_slot.isoformat()}",
                "job_cycle_scheduled",
                "scheduled",
                3,
                scheduled_window_end,
            ),
        ]

        account_rows = connection.execute(
            text(
                """
                SELECT cycle_key, account_id, position
                FROM automation_run_cycle_accounts
                ORDER BY cycle_key, position
                """
            )
        ).all()
        assert account_rows == [
            ("manual:job_cycle_manual:cycle-1", "acc_cycle_manual_a", 0),
            ("manual:job_cycle_manual:cycle-1", "acc_cycle_manual_b", 1),
            (f"scheduled:job_cycle_scheduled:{scheduled_due_slot.isoformat()}", "acc_cycle_scheduled_a", 0),
            (f"scheduled:job_cycle_scheduled:{scheduled_due_slot.isoformat()}", "acc_cycle_scheduled_c", 1),
            (f"scheduled:job_cycle_scheduled:{scheduled_due_slot.isoformat()}", "acc_cycle_scheduled_b", 2),
        ]


def test_check_schema_drift_detects_rogue_table(tmp_path: Path) -> None:
    db_path = tmp_path / "drift.db"
    url = _db_url(db_path)

    run_upgrade(url, "head", bootstrap_legacy=False)
    assert check_schema_drift(url) == ()

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).connect() as connection:
        connection.execute(text("CREATE TABLE rogue_table (id INTEGER PRIMARY KEY)"))
        connection.commit()

    drift = check_schema_drift(url)
    assert drift
    assert any("rogue_table" in diff for diff in drift)


def test_check_schema_drift_ignores_legacy_live_extra_request_log_column(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-extra-column.db"
    url = _db_url(db_path)

    run_upgrade(url, "head", bootstrap_legacy=False)

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).connect() as connection:
        connection.execute(text("ALTER TABLE request_logs ADD COLUMN slim_summary_json TEXT"))
        connection.commit()

    assert check_schema_drift(url) == ()


def test_check_schema_drift_ignores_sqlite_real_float_reflection_for_sticky_thresholds(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "sqlite-real-float.db"
    url = _db_url(db_path)

    run_upgrade(url, "head", bootstrap_legacy=False)

    def _compare_metadata(context, metadata):
        return [
            [
                (
                    "modify_type",
                    None,
                    "dashboard_settings",
                    "sticky_reallocation_primary_budget_threshold_pct",
                    {},
                    sa.REAL(),
                    sa.Float(),
                )
            ],
            [
                (
                    "modify_type",
                    None,
                    "dashboard_settings",
                    "sticky_reallocation_secondary_budget_threshold_pct",
                    {},
                    sa.REAL(),
                    sa.Float(),
                )
            ],
        ]

    monkeypatch.setattr(migrate_module, "compare_metadata", _compare_metadata)

    assert check_schema_drift(url) == ()


def test_check_schema_drift_detects_missing_manual_performance_index(tmp_path: Path) -> None:
    db_path = tmp_path / "missing-index.db"
    url = _db_url(db_path)

    run_upgrade(url, "head", bootstrap_legacy=False)

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).connect() as connection:
        connection.execute(text("DROP INDEX idx_usage_window_account_latest"))
        connection.execute(text("DROP INDEX idx_usage_window_raw_account_latest"))
        connection.commit()

    drift = check_schema_drift(url)
    assert any("idx_usage_window_account_latest" in diff for diff in drift)
    assert any("idx_usage_window_raw_account_latest" in diff for diff in drift)


def test_raw_usage_window_latest_index_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "raw-window-latest-index.db"
    url = _db_url(db_path)
    pre_revision = "20260513_000000_add_accounts_alias"
    target_revision = "20260525_000000_add_usage_raw_window_latest_index"

    run_upgrade(url, pre_revision, bootstrap_legacy=False)

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).connect() as connection:
        connection.execute(
            text(
                """
                CREATE INDEX idx_usage_window_raw_account_latest
                ON usage_history ("window", account_id, recorded_at DESC, id DESC)
                """
            )
        )
        connection.commit()

    result = run_upgrade(url, target_revision, bootstrap_legacy=False)
    assert result.current_revision == target_revision

    with create_engine(sync_url, future=True).connect() as connection:
        index_names = {index["name"] for index in inspect(connection).get_indexes("usage_history")}
        assert "idx_usage_window_raw_account_latest" in index_names


def test_check_schema_drift_detects_missing_dashboard_read_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "missing-dashboard-read-indexes.db"
    url = _db_url(db_path)

    run_upgrade(url, "head", bootstrap_legacy=False)

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).connect() as connection:
        connection.execute(text("DROP INDEX idx_usage_window_account_time"))
        connection.execute(text("DROP INDEX idx_logs_requested_at_model_tier"))
        connection.execute(text("DROP INDEX idx_logs_model_effort_time"))
        connection.execute(text("DROP INDEX idx_logs_status_error_time"))
        connection.execute(text("DROP INDEX idx_api_keys_name"))
        connection.commit()

    drift = check_schema_drift(url)
    assert any("idx_usage_window_account_time" in diff for diff in drift)
    assert any("idx_logs_requested_at_model_tier" in diff for diff in drift)
    assert any("idx_logs_model_effort_time" in diff for diff in drift)
    assert any("idx_logs_status_error_time" in diff for diff in drift)
    assert any("idx_api_keys_name" in diff for diff in drift)


def test_run_upgrade_auto_remaps_legacy_revision_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "remap.db"
    url = _db_url(db_path)

    initial = run_upgrade(url, "head", bootstrap_legacy=False)
    assert initial.current_revision is not None

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).begin() as connection:
        connection.execute(
            text("UPDATE alembic_version SET version_num = :legacy"),
            {"legacy": "013_add_dashboard_settings_routing_strategy"},
        )

    result = run_upgrade(url, "head", bootstrap_legacy=False)
    assert result.current_revision == initial.current_revision


def test_run_upgrade_auto_remaps_legacy_routing_security_merge_head(tmp_path: Path) -> None:
    db_path = tmp_path / "routing-security-remap.db"
    url = _db_url(db_path)

    initial = run_upgrade(url, "head", bootstrap_legacy=False)
    assert initial.current_revision is not None

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).begin() as connection:
        connection.execute(
            text("UPDATE alembic_version SET version_num = :legacy"),
            {"legacy": "20260525_000000_merge_routing_settings_security_heads"},
        )

    result = run_upgrade(url, "head", bootstrap_legacy=False)
    assert result.current_revision == initial.current_revision


def test_run_upgrade_without_auto_remap_fails_for_legacy_revision_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "no-remap.db"
    url = _db_url(db_path)

    run_upgrade(url, "head", bootstrap_legacy=False)

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).begin() as connection:
        connection.execute(
            text("UPDATE alembic_version SET version_num = :legacy"),
            {"legacy": "013_add_dashboard_settings_routing_strategy"},
        )

    with pytest.raises(CommandError, match="Can't locate revision identified by"):
        run_upgrade(url, "head", bootstrap_legacy=False, auto_remap_legacy_revisions=False)


def test_run_upgrade_repairs_branched_legacy_revision_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "branch-repair.db"
    url = _db_url(db_path)

    ancestor = "20260218_000100_add_import_without_overwrite_and_drop_accounts_email_unique"
    run_upgrade(url, ancestor, bootstrap_legacy=False)

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).begin() as connection:
        connection.execute(text("ALTER TABLE api_keys ADD COLUMN enforced_model VARCHAR"))
        connection.execute(text("ALTER TABLE api_keys ADD COLUMN enforced_reasoning_effort VARCHAR"))
        connection.execute(
            text("UPDATE alembic_version SET version_num = :legacy"),
            {"legacy": "013_add_api_key_enforcement_fields"},
        )

    result = run_upgrade(url, "head", bootstrap_legacy=False)
    assert result.current_revision is not None

    with create_engine(sync_url, future=True).connect() as connection:
        inspector = inspect(connection)
        dashboard_columns = {column["name"] for column in inspector.get_columns("dashboard_settings")}
        api_key_columns = {column["name"] for column in inspector.get_columns("api_keys")}

        assert "routing_strategy" in dashboard_columns
        assert "relative_availability_power" in dashboard_columns
        assert "relative_availability_top_k" in dashboard_columns
        assert "enforced_model" in api_key_columns
        assert "enforced_reasoning_effort" in api_key_columns
        assert inspector.has_table("api_firewall_allowlist")


def test_run_upgrade_repairs_branched_legacy_revision_ids_with_parallel_head(tmp_path: Path) -> None:
    db_path = tmp_path / "branch-repair-parallel.db"
    url = _db_url(db_path)

    run_upgrade(url, "20260228_030000_add_api_firewall_allowlist", bootstrap_legacy=False)

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).begin() as connection:
        connection.execute(text("ALTER TABLE api_keys ADD COLUMN enforced_model VARCHAR"))
        connection.execute(text("ALTER TABLE api_keys ADD COLUMN enforced_reasoning_effort VARCHAR"))
        connection.execute(text("DELETE FROM alembic_version"))
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": "013_add_api_key_enforcement_fields"},
        )
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": "014_add_api_firewall_allowlist"},
        )

    result = run_upgrade(url, "head", bootstrap_legacy=False)
    assert result.current_revision == inspect_migration_state(url).head_revision


def test_api_key_enforced_service_tier_column_exists_after_head_upgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "api-key-service-tier.db"
    url = _db_url(db_path)

    run_upgrade(url, "head", bootstrap_legacy=False)

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).connect() as connection:
        api_key_columns = {column["name"] for column in inspect(connection).get_columns("api_keys")}

    assert "enforced_service_tier" in api_key_columns


def test_run_upgrade_backfills_additional_usage_quota_key_from_configured_registry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quota-registry.db"
    url = _db_url(db_path)
    registry_path = tmp_path / "additional_quota_registry.json"
    registry_path.write_text(
        json.dumps(
            [
                {
                    "quota_key": "spark_enterprise",
                    "display_label": "Spark Enterprise",
                    "limit_name_aliases": ["codex_other"],
                    "metered_feature_aliases": ["codex_bengalfox"],
                }
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("CODEX_LB_ADDITIONAL_QUOTA_REGISTRY_FILE", str(registry_path))
    clear_additional_quota_registry_cache()

    run_upgrade(url, "20260309_000000_add_additional_usage_history", bootstrap_legacy=False)

    sync_url = to_sync_database_url(url)
    recorded_at = datetime.now(timezone.utc)
    with create_engine(sync_url, future=True).begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO accounts (
                    id,
                    email,
                    plan_type,
                    access_token_encrypted,
                    refresh_token_encrypted,
                    id_token_encrypted,
                    last_refresh,
                    status,
                    deactivation_reason,
                    chatgpt_account_id,
                    reset_at
                ) VALUES (
                    :id,
                    :email,
                    :plan_type,
                    :access_token_encrypted,
                    :refresh_token_encrypted,
                    :id_token_encrypted,
                    :last_refresh,
                    :status,
                    :deactivation_reason,
                    :chatgpt_account_id,
                    :reset_at
                )
                """
            ),
            {
                "id": "acc_registry",
                "email": "registry@example.com",
                "plan_type": "plus",
                "access_token_encrypted": b"access",
                "refresh_token_encrypted": b"refresh",
                "id_token_encrypted": b"id",
                "last_refresh": recorded_at,
                "status": "active",
                "deactivation_reason": None,
                "chatgpt_account_id": None,
                "reset_at": None,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO additional_usage_history (
                    account_id,
                    limit_name,
                    metered_feature,
                    window,
                    used_percent,
                    reset_at,
                    window_minutes,
                    recorded_at
                ) VALUES (
                    :account_id,
                    :limit_name,
                    :metered_feature,
                    :window,
                    :used_percent,
                    :reset_at,
                    :window_minutes,
                    :recorded_at
                )
                """
            ),
            {
                "account_id": "acc_registry",
                "limit_name": "codex_other",
                "metered_feature": "codex_bengalfox",
                "window": "primary",
                "used_percent": 12.5,
                "reset_at": None,
                "window_minutes": 60,
                "recorded_at": recorded_at,
            },
        )

    run_upgrade(url, "head", bootstrap_legacy=False)

    with create_engine(sync_url, future=True).connect() as connection:
        quota_key = connection.execute(text("SELECT quota_key FROM additional_usage_history")).scalar_one()

    assert quota_key == "spark_enterprise"
    clear_additional_quota_registry_cache()


def test_run_upgrade_rejects_duplicate_additional_quota_aliases_in_registry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quota-registry-invalid.db"
    url = _db_url(db_path)
    registry_path = tmp_path / "additional_quota_registry.json"
    registry_path.write_text(
        json.dumps(
            [
                {
                    "quota_key": "spark_enterprise",
                    "display_label": "Spark Enterprise",
                    "limit_name_aliases": ["codex_other"],
                },
                {
                    "quota_key": "spark_enterprise_backup",
                    "display_label": "Spark Enterprise Backup",
                    "limit_name_aliases": ["codex_other"],
                },
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("CODEX_LB_ADDITIONAL_QUOTA_REGISTRY_FILE", str(registry_path))
    clear_additional_quota_registry_cache()

    run_upgrade(url, "20260309_000000_add_additional_usage_history", bootstrap_legacy=False)

    with pytest.raises(ValueError, match="duplicate additional quota alias"):
        run_upgrade(url, "head", bootstrap_legacy=False)

    clear_additional_quota_registry_cache()


def test_run_upgrade_fails_for_unsupported_alembic_version_id(tmp_path: Path) -> None:
    db_path = tmp_path / "unsupported.db"
    url = _db_url(db_path)

    run_upgrade(url, "head", bootstrap_legacy=False)

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num = 'legacy_custom_999'"))

    with pytest.raises(MigrationBootstrapError, match="not known to this build"):
        run_upgrade(url, "head", bootstrap_legacy=False)


def test_check_migration_policy_reports_head_and_format_violations(monkeypatch, tmp_path: Path) -> None:
    class _FakeRevision:
        def __init__(self, revision: str, path: str) -> None:
            self.revision = revision
            self.path = path

    class _FakeScriptDirectory:
        def get_heads(self) -> list[str]:
            return ["head_a", "head_b"]

        def walk_revisions(self) -> list[_FakeRevision]:
            return [
                _FakeRevision("invalid-revision-id", "/tmp/not-matching-name.py"),
            ]

    fake_script_dir = _FakeScriptDirectory()
    monkeypatch.setattr("app.db.migrate.ScriptDirectory.from_config", lambda _: fake_script_dir)

    config = _build_alembic_config(_db_url(tmp_path / "policy.db"))
    violations = _collect_migration_policy_violations(config)

    assert any("alembic_head_count_invalid" in violation for violation in violations)
    assert any("alembic_revision_id_format_invalid" in violation for violation in violations)
    assert any("alembic_revision_filename_mismatch" in violation for violation in violations)

    wrapper_violations = check_migration_policy(_db_url(tmp_path / "policy-wrapper.db"))
    assert wrapper_violations == violations


def test_create_sqlite_pre_migration_backup_rotates_old_files(tmp_path: Path) -> None:
    db_path = tmp_path / "store.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        conn.execute("INSERT INTO items (name) VALUES ('alpha')")

    created: list[Path] = []
    base_time = datetime(2026, 2, 13, 12, 0, 0, tzinfo=timezone.utc)

    for index in range(3):
        backup = create_sqlite_pre_migration_backup(
            db_path,
            max_files=2,
            now=base_time + timedelta(minutes=index),
        )
        created.append(backup)

    backups = list_sqlite_pre_migration_backups(db_path)
    assert len(backups) == 2
    assert backups == created[-2:]
    for backup in backups:
        with sqlite3.connect(backup) as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert conn.execute("SELECT name FROM items").fetchall() == [("alpha",)]
        assert not Path(f"{backup}-wal").exists()


def test_create_sqlite_pre_migration_backup_consolidates_wal_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "store.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        conn.execute("INSERT INTO items (name) VALUES ('from-wal')")
        conn.commit()
        assert Path(f"{db_path}-wal").exists()

        backup = create_sqlite_pre_migration_backup(
            db_path,
            max_files=2,
            now=datetime(2026, 2, 13, 12, 0, 0, tzinfo=timezone.utc),
        )

    with sqlite3.connect(backup) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert conn.execute("SELECT name FROM items").fetchall() == [("from-wal",)]
    assert not Path(f"{backup}-wal").exists()


def test_create_sqlite_pre_migration_backup_preserves_source_mode(tmp_path: Path) -> None:
    db_path = tmp_path / "store.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    db_path.chmod(0o600)

    backup = create_sqlite_pre_migration_backup(
        db_path,
        max_files=2,
        now=datetime(2026, 2, 13, 12, 0, 0, tzinfo=timezone.utc),
    )

    assert backup.stat().st_mode & 0o777 == 0o600


class _FakeStringType:
    def __init__(self, length: int | None) -> None:
        self.length = length


class _FakeConnection:
    def __init__(self, *, dialect_name: str = "postgresql") -> None:
        self.dialect = SimpleNamespace(name=dialect_name)
        self.executed_sql: list[str] = []

    def execute(self, statement: object) -> None:
        self.executed_sql.append(str(statement))


class _MissingAlembicVersionConnection:
    def execute(self, statement: object) -> None:
        raise sa_exc.ProgrammingError(
            str(statement),
            {},
            Exception('relation "alembic_version" does not exist'),
        )


class _MissingAlembicVersionSQLiteConnection:
    def execute(self, statement: object) -> None:
        raise sa_exc.OperationalError(
            str(statement),
            {},
            Exception("no such table: alembic_version"),
        )


class _FakeInspector:
    def __init__(self, *, has_table: bool, version_num_length: int | None = None) -> None:
        self._has_table = has_table
        self._version_num_length = version_num_length

    def has_table(self, table_name: str) -> bool:
        assert table_name == "alembic_version"
        return self._has_table

    def get_columns(self, table_name: str) -> list[dict[str, object]]:
        assert table_name == "alembic_version"
        return [
            {
                "name": "version_num",
                "type": _FakeStringType(self._version_num_length),
            }
        ]


def test_ensure_alembic_version_table_capacity_creates_table_when_missing(monkeypatch) -> None:
    connection = _FakeConnection()
    inspector = _FakeInspector(has_table=False)
    monkeypatch.setattr("app.db.migrate.inspect", lambda _: inspector)

    _ensure_alembic_version_table_capacity_for_connection(cast(Connection, connection), required_length=64)

    assert connection.executed_sql == [
        "CREATE TABLE IF NOT EXISTS alembic_version ( version_num VARCHAR(64) NOT NULL, PRIMARY KEY (version_num) )"
    ]


def test_ensure_alembic_version_table_capacity_alters_short_column(monkeypatch) -> None:
    connection = _FakeConnection()
    inspector = _FakeInspector(has_table=True, version_num_length=32)
    monkeypatch.setattr("app.db.migrate.inspect", lambda _: inspector)

    _ensure_alembic_version_table_capacity_for_connection(cast(Connection, connection), required_length=64)

    assert connection.executed_sql == ["ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64)"]


def test_read_current_revisions_returns_empty_when_alembic_version_table_is_missing() -> None:
    connection = _MissingAlembicVersionConnection()

    assert _read_current_revisions_from_connection(cast(Connection, connection)) == ()


def test_read_current_revisions_returns_empty_when_alembic_version_table_is_missing_on_sqlite() -> None:
    connection = _MissingAlembicVersionSQLiteConnection()

    assert _read_current_revisions_from_connection(cast(Connection, connection)) == ()


def test_max_revision_id_length_exceeds_alembic_default(tmp_path: Path) -> None:
    db_path = tmp_path / "length-check.db"
    config = _build_alembic_config(_db_url(db_path))

    assert _max_revision_id_length(config) > 32


def test_routing_policy_persistence_downgrade_does_not_drop_shared_columns(monkeypatch) -> None:
    migration = importlib.import_module("app.db.alembic.versions.20260601_010000_add_routing_policy_persistence")

    class _OpMustNotAlter:
        def batch_alter_table(self, table_name: str):  # pragma: no cover - assertion helper
            raise AssertionError(f"unexpected schema alteration for {table_name}")

        def get_bind(self):  # pragma: no cover - assertion helper
            raise AssertionError("downgrade should not inspect a bind")

    monkeypatch.setattr(migration, "op", _OpMustNotAlter())

    migration.downgrade()


def test_replica_guardrails_migration_round_trips_with_version_backfill(tmp_path: Path) -> None:
    from alembic.script import ScriptDirectory

    db_path = tmp_path / "replica-guardrails.db"
    url = _db_url(db_path)
    parent_revision = "20260712_020000_add_api_key_usage_rollups"
    target_revision = "20260713_040000_add_replica_guardrails"

    run_upgrade(url, parent_revision, bootstrap_legacy=False)
    config = _build_alembic_config(url)

    script_directory = ScriptDirectory.from_config(config)
    # The replica-guardrails migration now sits beneath later revisions (e.g.
    # the reset-credit redeem tables re-parented onto it), so it is an ancestor
    # of the single graph head rather than the head itself.
    heads = script_directory.get_heads()
    assert len(heads) == 1
    ancestry = {script.revision for script in script_directory.walk_revisions()}
    assert target_revision in ancestry

    engine = create_engine(to_sync_database_url(url))
    try:
        with engine.connect() as connection:
            # The parent revision already seeds the singleton settings row.
            assert connection.execute(text("SELECT COUNT(*) FROM dashboard_settings")).scalar_one() == 1

        command.upgrade(config, target_revision)
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert inspector.has_table("runtime_sentinels")
            sentinel_columns = {column["name"] for column in inspector.get_columns("runtime_sentinels")}
            assert {"name", "value", "created_at", "updated_at"} <= sentinel_columns
            # Historical rows are backfilled to version 1 via the server default.
            assert connection.execute(text("SELECT version FROM dashboard_settings WHERE id = 1")).scalar_one() == 1

        command.downgrade(config, parent_revision)
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert not inspector.has_table("runtime_sentinels")
            assert "version" not in {column["name"] for column in inspector.get_columns("dashboard_settings")}
            assert connection.execute(text("SELECT COUNT(*) FROM dashboard_settings")).scalar_one() == 1
    finally:
        engine.dispose()


def test_check_schema_drift_detects_missing_dashboard_hot_path_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "missing-hot-path-indexes.db"
    url = _db_url(db_path)

    run_upgrade(url, "head", bootstrap_legacy=False)

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).connect() as connection:
        connection.execute(text("DROP INDEX idx_logs_dash_usage_covering"))
        connection.execute(text("DROP INDEX ix_additional_usage_distinct_labels"))
        connection.commit()

    drift = check_schema_drift(url)
    assert any("idx_logs_dash_usage_covering" in diff for diff in drift)
    assert any("ix_additional_usage_distinct_labels" in diff for diff in drift)


def test_dashboard_hot_path_index_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "hot-path-indexes.db"
    url = _db_url(db_path)
    pre_revision = "20260716_010000_add_dashboard_retention_settings"
    target_revision = "20260717_000000_optimize_dashboard_hot_path_indexes"

    run_upgrade(url, pre_revision, bootstrap_legacy=False)

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).connect() as connection:
        connection.execute(
            text(
                """
                CREATE INDEX idx_logs_dash_usage_covering
                ON request_logs (requested_at)
                WHERE deleted_at IS NULL
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE INDEX ix_additional_usage_distinct_labels
                ON additional_usage_history (account_id, quota_key, limit_name, metered_feature)
                """
            )
        )
        connection.commit()

    result = run_upgrade(url, target_revision, bootstrap_legacy=False)
    assert result.current_revision == target_revision

    with create_engine(sync_url, future=True).connect() as connection:
        log_indexes = {str(row[1]) for row in connection.execute(text('PRAGMA index_list("request_logs")')).fetchall()}
        usage_indexes = {
            str(row[1]) for row in connection.execute(text('PRAGMA index_list("additional_usage_history")')).fetchall()
        }

    assert "idx_logs_dash_usage_covering" in log_indexes
    assert "ix_additional_usage_distinct_labels" in usage_indexes


def test_dashboard_hot_path_postgresql_indexes_build_concurrently() -> None:
    revision_path = (
        Path(__file__).resolve().parents[2]
        / "app/db/alembic/versions/20260717_000000_optimize_dashboard_hot_path_indexes.py"
    )
    source = revision_path.read_text(encoding="utf-8")

    assert "autocommit_block" in source
    assert source.count("CREATE INDEX CONCURRENTLY IF NOT EXISTS") == 2
    assert "_COVERING_INDEX_NAME" in source
    assert "_LABELS_INDEX_NAME" in source
    # Redundant indexes are dropped without blocking writers, and leftover
    # invalid indexes from an interrupted concurrent build are rebuilt instead
    # of being silently accepted by IF NOT EXISTS.
    assert source.count("DROP INDEX CONCURRENTLY IF EXISTS") >= 2
    assert "indisvalid" in source


def test_dashboard_hot_path_index_migration_drops_redundant_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "hot-path-redundant-indexes.db"
    url = _db_url(db_path)

    run_upgrade(url, "head", bootstrap_legacy=False)

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).connect() as connection:
        log_indexes = {str(row[1]) for row in connection.execute(text('PRAGMA index_list("request_logs")')).fetchall()}
        usage_indexes = {
            str(row[1]) for row in connection.execute(text('PRAGMA index_list("additional_usage_history")')).fetchall()
        }

    assert "idx_logs_requested_at" not in log_indexes
    assert "idx_logs_api_key_time_account" not in log_indexes
    assert "ix_additional_usage_history_account_id" not in usage_indexes
    # Kept: the sessionless response-owner fallback needs its ordered retrieval.
    assert "idx_logs_request_status_api_key_time" in log_indexes
    assert "idx_logs_dash_usage_covering" in log_indexes
    assert "ix_additional_usage_distinct_labels" in usage_indexes

    assert check_schema_drift(url) == ()
