from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import text as sa_text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.db.session as session_module
from app.db.models import Account, AccountStatus, Base
from app.db.sqlite_utils import (
    IntegrityCheck,
    SqliteFileIdentity,
    SqliteIntegrityCheckMode,
    SqliteRunState,
    SqliteRunStateDurabilityError,
    acquire_sqlite_runstate_lock,
    read_sqlite_runstate,
    release_sqlite_runstate_lock,
    write_sqlite_runstate,
)


@dataclass(slots=True)
class _FakeSettings:
    database_url: str
    database_pool_size: int = 15
    database_max_overflow: int = 10
    database_migrate_on_startup: bool = True
    database_sqlite_pre_migrate_backup_enabled: bool = False
    database_sqlite_pre_migrate_backup_max_files: int = 5
    database_sqlite_startup_check_mode: str = "quick"
    database_migrations_fail_fast: bool = False


@dataclass(slots=True)
class _FakeMigrationState:
    current_revision: str | None
    head_revision: str
    has_alembic_version_table: bool
    has_legacy_migrations_table: bool
    needs_upgrade: bool
    unknown_revisions: tuple[str, ...] = ()
    is_ahead: bool = False


@dataclass(slots=True)
class _FakeBootstrap:
    stamped_revision: str | None = None
    legacy_row_count: int = 0


@dataclass(slots=True)
class _FakeMigrationRunResult:
    current_revision: str | None = "head"
    bootstrap: _FakeBootstrap = field(default_factory=_FakeBootstrap)


def test_import_session_with_sqlite_memory_url_does_not_error() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["CODEX_LB_DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

    result = subprocess.run(
        [sys.executable, "-c", "import sys; import app.db.session; assert 'app.db.migrate' not in sys.modules"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_import_session_with_postgres_url_does_not_error() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["CODEX_LB_DATABASE_URL"] = "postgresql+asyncpg://codex_lb:codex_lb@127.0.0.1:5432/codex_lb"

    result = subprocess.run(
        [sys.executable, "-c", "import app.db.session"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.asyncio
async def test_sqlite_writer_section_serializes_file_sqlite_writers(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'store.db'}"),
    )
    monkeypatch.setattr(session_module, "_sqlite_writer_lock", None)
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    order: list[str] = []

    async def first_writer() -> None:
        async with session_module.sqlite_writer_section():
            order.append("first-start")
            first_entered.set()
            await release_first.wait()
            order.append("first-end")

    async def second_writer() -> None:
        async with session_module.sqlite_writer_section():
            order.append("second-start")
            order.append("second-end")

    first_task = asyncio.create_task(first_writer())
    await first_entered.wait()
    second_task = asyncio.create_task(second_writer())
    await asyncio.sleep(0)

    assert order == ["first-start"]

    release_first.set()
    await asyncio.gather(first_task, second_task)

    assert order == ["first-start", "first-end", "second-start", "second-end"]


@pytest.mark.asyncio
async def test_sqlite_writer_section_does_not_serialize_memory_sqlite(monkeypatch) -> None:
    monkeypatch.setattr(session_module, "_settings", _FakeSettings(database_url="sqlite+aiosqlite:///:memory:"))
    monkeypatch.setattr(session_module, "_sqlite_writer_lock", None)
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()

    async def first_writer() -> None:
        async with session_module.sqlite_writer_section():
            first_entered.set()
            await second_entered.wait()

    async def second_writer() -> None:
        await first_entered.wait()
        async with session_module.sqlite_writer_section():
            second_entered.set()

    await asyncio.wait_for(asyncio.gather(first_writer(), second_writer()), timeout=1)


def test_postgres_engine_kwargs_enable_pre_ping_and_recycle(monkeypatch) -> None:
    """Regression for #672: PostgreSQL engines MUST validate pooled connections
    on checkout (``pool_pre_ping``) and recycle them within a finite window
    (``pool_recycle``). Without these the pool serves stale connections
    after the server idles them out, causing
    ``asyncpg.InterfaceError: connection is closed`` on the first real query.

    Both the main and the background engine build their kwargs through this
    single helper, so one assertion covers both engines.
    """
    monkeypatch.setenv("CODEX_LB_TEST_DATABASE_URL", "")
    monkeypatch.delenv("CODEX_LB_TEST_DATABASE_URL", raising=False)
    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url="postgresql+asyncpg://u:p@h/db",
            database_pool_size=15,
            database_max_overflow=10,
        ),
    )

    kwargs = session_module._postgres_async_engine_kwargs("postgresql+asyncpg://u:p@h/db")
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_recycle"] == 1800
    assert kwargs["pool_size"] == 15
    assert kwargs["max_overflow"] == 10
    assert kwargs["pool_timeout"] == 30.0


def test_postgres_engine_kwargs_use_fixed_timeout_and_recycle_constants(monkeypatch) -> None:
    monkeypatch.delenv("CODEX_LB_TEST_DATABASE_URL", raising=False)
    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(database_url="postgresql+asyncpg://u:p@h/db"),
    )

    kwargs = session_module._postgres_async_engine_kwargs("postgresql+asyncpg://u:p@h/db")
    assert kwargs["pool_timeout"] == session_module._POSTGRES_POOL_TIMEOUT_SECONDS == 30.0
    assert kwargs["pool_recycle"] == session_module._POSTGRES_POOL_RECYCLE_SECONDS == 1800


def test_postgres_engine_factory_rejects_undeclared_role() -> None:
    with pytest.raises(TypeError, match="must be declared"):
        session_module._create_postgres_async_engine(
            "postgresql+asyncpg://u:p@h/db",
            role=cast(Any, "undeclared"),
        )


@pytest.mark.asyncio
async def test_postgres_creation_paths_cover_every_budgeted_engine_role_once(monkeypatch) -> None:
    database_url = "postgresql+asyncpg://u:p@h/db"
    created_roles: list[session_module._PostgresPooledEngineRole] = []
    original_factory = session_module._create_postgres_async_engine

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url=database_url,
            database_pool_size=3,
            database_max_overflow=2,
        ),
    )
    monkeypatch.delenv("CODEX_LB_TEST_DATABASE_URL", raising=False)

    def recording_factory(
        url: str,
        *,
        role: session_module._PostgresPooledEngineRole,
    ) -> session_module.AsyncEngine:
        created_roles.append(role)
        return original_factory(url, role=role)

    monkeypatch.setattr(session_module, "_create_postgres_async_engine", recording_factory)
    main_engine = session_module._create_main_engine(database_url)
    try:
        session_module.init_background_db(database_url)

        assert tuple(created_roles) == session_module._POSTGRES_POOLED_ENGINE_ROLES
        assert session_module._POSTGRES_POOLED_ENGINES_PER_WORKER == len(created_roles) == 2
    finally:
        await main_engine.dispose()
        if session_module._background_engine is not None:
            await session_module._background_engine.dispose()
        session_module._background_engine = None
        session_module._background_session_factory = None


@pytest.mark.asyncio
async def test_postgres_test_engines_keep_declared_roles_but_disable_pooling(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_TEST_DATABASE_URL", "1")
    engines = [
        session_module._create_postgres_async_engine(
            "postgresql+asyncpg://u:p@h/db",
            role=role,
        )
        for role in session_module._POSTGRES_POOLED_ENGINE_ROLES
    ]

    try:
        assert all(isinstance(engine.pool, NullPool) for engine in engines)
    finally:
        for engine in engines:
            await engine.dispose()


def test_postgres_engine_kwargs_use_nullpool_under_test_db_url(monkeypatch) -> None:
    """The CODEX_LB_TEST_DATABASE_URL escape hatch keeps NullPool semantics —
    pool_pre_ping/recycle are irrelevant when each session opens a fresh
    connection.
    """
    monkeypatch.setenv("CODEX_LB_TEST_DATABASE_URL", "1")
    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(database_url="postgresql+asyncpg://u:p@h/db"),
    )

    kwargs = session_module._postgres_async_engine_kwargs("postgresql+asyncpg://u:p@h/db")
    assert kwargs["poolclass"] is NullPool
    assert "pool_pre_ping" not in kwargs
    assert "pool_recycle" not in kwargs


def test_sqlite_file_engine_kwargs_use_nullpool_without_pool_controls(monkeypatch) -> None:
    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url="sqlite+aiosqlite:///store.db",
            database_pool_size=15,
            database_max_overflow=10,
        ),
    )

    kwargs = session_module._sqlite_file_async_engine_kwargs()

    assert kwargs["poolclass"] is NullPool
    assert kwargs["connect_args"] == {"timeout": 30.0}
    assert "pool_size" not in kwargs
    assert "max_overflow" not in kwargs
    assert "pool_timeout" not in kwargs


def test_postgres_engine_kwargs_keep_pool_controls(monkeypatch) -> None:
    monkeypatch.delenv("CODEX_LB_TEST_DATABASE_URL", raising=False)
    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url="postgresql+asyncpg://u:p@h/db",
            database_pool_size=12,
            database_max_overflow=4,
        ),
    )

    kwargs = session_module._postgres_async_engine_kwargs("postgresql+asyncpg://u:p@h/db")

    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_recycle"] == 1800
    assert kwargs["pool_size"] == 12
    assert kwargs["max_overflow"] == 4
    assert kwargs["pool_timeout"] == 30.0


def test_postgres_connect_args_pin_session_timezone_to_utc(monkeypatch) -> None:
    """Regression: the application writes naive UTC datetimes into timestamptz
    columns, so the asyncpg session time zone MUST be UTC. Otherwise a container
    running e.g. TZ=Europe/Amsterdam makes PostgreSQL interpret those naive
    values in local time and shift every stored timestamp, which silently breaks
    ring-membership staleness, leader election and bridge-session lease expiry.
    """
    monkeypatch.delenv("CODEX_LB_TEST_DATABASE_URL", raising=False)

    connect_args = session_module._postgres_async_connect_args("postgresql+asyncpg://u:p@h/db")

    assert connect_args == {
        "server_settings": {"timezone": "UTC"},
        "command_timeout": session_module._POSTGRES_COMMAND_TIMEOUT_SECONDS,
    }


def test_postgres_connect_args_pin_utc_and_keep_test_db_url_tuning(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_TEST_DATABASE_URL", "1")

    connect_args = session_module._postgres_async_connect_args("postgresql+asyncpg://u:p@h/db")

    assert connect_args == {
        "server_settings": {"timezone": "UTC"},
        "command_timeout": session_module._POSTGRES_COMMAND_TIMEOUT_SECONDS,
        "prepared_statement_cache_size": 0,
    }


def test_postgres_connect_args_none_for_non_postgres_url() -> None:
    assert session_module._postgres_async_connect_args("sqlite+aiosqlite:///:memory:") is None


def test_postgres_engine_kwargs_forward_utc_connect_args(monkeypatch) -> None:
    monkeypatch.delenv("CODEX_LB_TEST_DATABASE_URL", raising=False)
    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(database_url="postgresql+asyncpg://u:p@h/db"),
    )

    kwargs = session_module._postgres_async_engine_kwargs("postgresql+asyncpg://u:p@h/db")

    assert kwargs["connect_args"] == {
        "server_settings": {"timezone": "UTC"},
        "command_timeout": session_module._POSTGRES_COMMAND_TIMEOUT_SECONDS,
    }


@pytest.mark.asyncio
async def test_close_session_rolls_back_open_transaction_before_close() -> None:
    calls: list[str] = []

    class _Session:
        def in_transaction(self) -> bool:
            return True

        async def rollback(self) -> None:
            calls.append("rollback")

        async def close(self) -> None:
            calls.append("close")

    await session_module.close_session(cast(Any, _Session()))

    assert calls == ["rollback", "close"]


@pytest.mark.asyncio
async def test_close_session_outlives_caller_cancellation() -> None:
    rollback_started = asyncio.Event()
    rollback_release = asyncio.Event()
    close_started = asyncio.Event()
    close_release = asyncio.Event()
    cleanup_done = asyncio.Event()
    calls: list[str] = []

    class FakeSession:
        def in_transaction(self) -> bool:
            return True

        async def rollback(self) -> None:
            calls.append("rollback-start")
            rollback_started.set()
            await rollback_release.wait()
            calls.append("rollback-end")

        async def close(self) -> None:
            calls.append("close-start")
            close_started.set()
            await close_release.wait()
            calls.append("close-end")

    async def run_cleanup() -> None:
        try:
            await session_module.close_session(cast(session_module.AsyncSession, FakeSession()))
        finally:
            cleanup_done.set()

    async with asyncio.TaskGroup() as group:
        task = group.create_task(run_cleanup())
        await rollback_started.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert calls == ["rollback-start"]
        assert not cleanup_done.is_set()
        rollback_release.set()
        await close_started.wait()
        close_release.set()

    assert calls == ["rollback-start", "rollback-end", "close-start", "close-end"]
    assert cleanup_done.is_set()


@pytest.mark.asyncio
async def test_detach_session_objects_keeps_loaded_fields_available_after_rollback() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as session:
            session.add(
                Account(
                    id="acc_detached",
                    chatgpt_account_id="workspace-detached",
                    email="detached@example.com",
                    plan_type="plus",
                    access_token_encrypted=b"access",
                    refresh_token_encrypted=b"refresh",
                    id_token_encrypted=b"id",
                    last_refresh=datetime(2026, 1, 1),
                    status=AccountStatus.ACTIVE,
                )
            )
            await session.commit()

        async with session_factory() as session:
            account = await session.get(Account, "acc_detached")
            assert account is not None
            assert account.status == AccountStatus.ACTIVE
            session_module.detach_session_objects(session)
            await session.rollback()

        assert account.id == "acc_detached"
        assert account.status == AccountStatus.ACTIVE
        assert account.chatgpt_account_id == "workspace-detached"
        assert account.access_token_encrypted == b"access"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_init_db_fails_when_migration_module_is_missing_even_with_fail_fast_disabled(monkeypatch) -> None:
    def _raise_missing_migration() -> tuple[object, object]:
        raise ModuleNotFoundError("No module named 'app.db.migrate'", name="app.db.migrate")

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(database_url="sqlite+aiosqlite:///:memory:", database_migrations_fail_fast=False),
    )
    monkeypatch.setattr(session_module, "_load_migration_entrypoints", _raise_missing_migration)

    with pytest.raises(RuntimeError, match="app\\.db\\.migrate is unavailable"):
        await session_module.init_db()


@pytest.mark.asyncio
async def test_init_db_fails_when_migration_entrypoint_is_invalid_even_with_fail_fast_disabled(monkeypatch) -> None:
    def _raise_invalid_migration() -> tuple[object, object]:
        raise ImportError("cannot import name 'run_startup_migrations' from 'app.db.migrate'")

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(database_url="sqlite+aiosqlite:///:memory:", database_migrations_fail_fast=False),
    )
    monkeypatch.setattr(session_module, "_load_migration_entrypoints", _raise_invalid_migration)

    with pytest.raises(RuntimeError, match="app\\.db\\.migrate is invalid"):
        await session_module.init_db()


@pytest.mark.asyncio
async def test_init_db_fails_when_backup_module_is_missing_even_with_fail_fast_disabled(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"")

    def _inspect_migration_state(_: str) -> _FakeMigrationState:
        return _FakeMigrationState(
            current_revision=None,
            head_revision="head",
            has_alembic_version_table=False,
            has_legacy_migrations_table=False,
            needs_upgrade=True,
        )

    async def _run_startup_migrations(_: str) -> _FakeMigrationRunResult:
        return _FakeMigrationRunResult()

    def _check_schema_drift(_: str) -> tuple[str, ...]:
        return ()

    def _load_entrypoints() -> tuple[object, object, object]:
        return _inspect_migration_state, _run_startup_migrations, _check_schema_drift

    def _raise_missing_backup() -> object:
        raise ModuleNotFoundError("No module named 'app.db.backup'", name="app.db.backup")

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url=f"sqlite+aiosqlite:///{db_path}",
            database_sqlite_pre_migrate_backup_enabled=True,
            database_migrations_fail_fast=False,
        ),
    )
    monkeypatch.setattr(session_module, "_load_migration_entrypoints", _load_entrypoints)
    monkeypatch.setattr(session_module, "_load_sqlite_backup_creator", _raise_missing_backup)

    with pytest.raises(RuntimeError, match="app\\.db\\.backup is unavailable"):
        await session_module.init_db()


@pytest.mark.asyncio
async def test_init_db_fails_fast_on_post_migration_schema_drift(monkeypatch) -> None:
    async def _run_startup_migrations(_: str) -> _FakeMigrationRunResult:
        return _FakeMigrationRunResult()

    def _inspect_migration_state(_: str) -> _FakeMigrationState:
        return _FakeMigrationState(
            current_revision="head",
            head_revision="head",
            has_alembic_version_table=True,
            has_legacy_migrations_table=False,
            needs_upgrade=False,
        )

    def _check_schema_drift(_: str) -> tuple[str, ...]:
        return ("('add_table', 'additional_usage_history')",)

    def _load_entrypoints() -> tuple[object, object, object]:
        return _inspect_migration_state, _run_startup_migrations, _check_schema_drift

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url="sqlite+aiosqlite:///:memory:",
            database_migrations_fail_fast=True,
        ),
    )
    monkeypatch.setattr(session_module, "_load_migration_entrypoints", _load_entrypoints)

    with pytest.raises(RuntimeError, match="Schema drift detected after startup migrations"):
        await session_module.init_db()


@pytest.mark.asyncio
async def test_init_db_logs_post_migration_schema_drift_when_fail_fast_disabled(monkeypatch, caplog) -> None:
    async def _run_startup_migrations(_: str) -> _FakeMigrationRunResult:
        return _FakeMigrationRunResult()

    def _inspect_migration_state(_: str) -> _FakeMigrationState:
        return _FakeMigrationState(
            current_revision="head",
            head_revision="head",
            has_alembic_version_table=True,
            has_legacy_migrations_table=False,
            needs_upgrade=False,
        )

    def _check_schema_drift(_: str) -> tuple[str, ...]:
        return ("('missing_index', 'request_logs', 'idx_logs_requested_at_id')",)

    def _load_entrypoints() -> tuple[object, object, object]:
        return _inspect_migration_state, _run_startup_migrations, _check_schema_drift

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url="sqlite+aiosqlite:///:memory:",
            database_migrations_fail_fast=False,
        ),
    )
    monkeypatch.setattr(session_module, "_load_migration_entrypoints", _load_entrypoints)

    caplog.set_level(logging.ERROR)

    await session_module.init_db()

    assert "Failed to apply database migrations" in caplog.text
    assert "Schema drift detected after startup migrations" in caplog.text
    assert "idx_logs_requested_at_id" in caplog.text


@pytest.mark.asyncio
async def test_init_db_uses_quick_check_by_default(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")
    seen: list[SqliteIntegrityCheckMode] = []

    def _check(path: Path, *, mode: SqliteIntegrityCheckMode = SqliteIntegrityCheckMode.FULL) -> IntegrityCheck:
        assert path == db_path
        seen.append(mode)
        return IntegrityCheck(ok=True, details=None)

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url=f"sqlite+aiosqlite:///{db_path}",
            database_migrate_on_startup=False,
        ),
    )
    monkeypatch.setattr(session_module, "check_sqlite_integrity", _check)
    monkeypatch.setattr(
        session_module,
        "_load_migration_entrypoints",
        lambda: (
            lambda _: _FakeMigrationState(
                current_revision="head",
                head_revision="head",
                has_alembic_version_table=True,
                has_legacy_migrations_table=False,
                needs_upgrade=False,
            ),
            lambda _: (_ for _ in ()).throw(AssertionError("startup migrations should stay disabled")),
            lambda _: (),
        ),
    )

    await session_module.init_db()

    assert seen == [SqliteIntegrityCheckMode.QUICK]


@pytest.mark.asyncio
async def test_init_db_uses_full_check_when_configured(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")
    seen: list[SqliteIntegrityCheckMode] = []

    def _check(path: Path, *, mode: SqliteIntegrityCheckMode = SqliteIntegrityCheckMode.FULL) -> IntegrityCheck:
        assert path == db_path
        seen.append(mode)
        return IntegrityCheck(ok=True, details=None)

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url=f"sqlite+aiosqlite:///{db_path}",
            database_migrate_on_startup=False,
            database_sqlite_startup_check_mode="full",
        ),
    )
    monkeypatch.setattr(session_module, "check_sqlite_integrity", _check)
    monkeypatch.setattr(
        session_module,
        "_load_migration_entrypoints",
        lambda: (
            lambda _: _FakeMigrationState(
                current_revision="head",
                head_revision="head",
                has_alembic_version_table=True,
                has_legacy_migrations_table=False,
                needs_upgrade=False,
            ),
            lambda _: (_ for _ in ()).throw(AssertionError("startup migrations should stay disabled")),
            lambda _: (),
        ),
    )

    await session_module.init_db()

    assert seen == [SqliteIntegrityCheckMode.FULL]


@pytest.mark.asyncio
async def test_init_db_skips_sqlite_check_when_disabled(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")

    def _check(_: Path, *, mode: SqliteIntegrityCheckMode = SqliteIntegrityCheckMode.FULL) -> IntegrityCheck:
        raise AssertionError("sqlite startup check should be skipped when disabled")

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url=f"sqlite+aiosqlite:///{db_path}",
            database_migrate_on_startup=False,
            database_sqlite_startup_check_mode="off",
        ),
    )
    monkeypatch.setattr(session_module, "check_sqlite_integrity", _check)
    monkeypatch.setattr(
        session_module,
        "_load_migration_entrypoints",
        lambda: (
            lambda _: _FakeMigrationState(
                current_revision="head",
                head_revision="head",
                has_alembic_version_table=True,
                has_legacy_migrations_table=False,
                needs_upgrade=False,
            ),
            lambda _: (_ for _ in ()).throw(AssertionError("startup migrations should stay disabled")),
            lambda _: (),
        ),
    )

    await session_module.init_db()


@pytest.mark.asyncio
async def test_init_db_fails_when_startup_migrations_are_disabled_but_schema_is_behind(monkeypatch) -> None:
    def _inspect_migration_state(_: str) -> _FakeMigrationState:
        return _FakeMigrationState(
            current_revision="20260330_020000_add_bridge_ring_members",
            head_revision="20260401_000000_add_cache_invalidation",
            has_alembic_version_table=True,
            has_legacy_migrations_table=False,
            needs_upgrade=True,
        )

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url="sqlite+aiosqlite:///:memory:",
            database_migrate_on_startup=False,
        ),
    )
    monkeypatch.setattr(
        session_module,
        "_load_migration_entrypoints",
        lambda: (
            _inspect_migration_state,
            lambda _: (_ for _ in ()).throw(AssertionError("startup migrations should stay disabled")),
            lambda _: (),
        ),
    )

    with pytest.raises(RuntimeError, match="database schema is behind Alembic head"):
        await session_module.init_db()


@pytest.mark.asyncio
async def test_init_background_db_creates_separate_engine() -> None:
    session_module.init_background_db("sqlite+aiosqlite:///:memory:")

    assert session_module._background_engine is not None
    assert session_module._background_session_factory is not None

    await session_module._background_engine.dispose()
    session_module._background_engine = None
    session_module._background_session_factory = None


@pytest.mark.asyncio
async def test_init_background_db_derives_postgres_pool_size_from_main_pool() -> None:
    session_module.init_background_db("postgresql+asyncpg://user:pass@localhost/db")

    assert session_module._background_engine is not None
    assert session_module._background_session_factory is not None

    pool = session_module._background_engine.pool
    if os.environ.get("CODEX_LB_TEST_DATABASE_URL"):
        assert isinstance(pool, NullPool)
    else:
        assert cast(Any, pool).size() == 25

    if session_module._background_engine is not None:
        await session_module._background_engine.dispose()
    session_module._background_engine = None
    session_module._background_session_factory = None


@pytest.mark.asyncio
async def test_get_background_session_uses_background_pool_when_initialized() -> None:
    session_module.init_background_db("sqlite+aiosqlite:///:memory:")

    async with session_module.get_background_session() as session:
        assert session is not None
        assert isinstance(session, session_module.AsyncSession)

    if session_module._background_engine is not None:
        await session_module._background_engine.dispose()
    session_module._background_engine = None
    session_module._background_session_factory = None


@pytest.mark.asyncio
async def test_get_background_session_falls_back_to_main_pool_when_not_initialized() -> None:
    session_module._background_engine = None
    session_module._background_session_factory = None

    async with session_module.get_background_session() as session:
        assert session is not None
        assert isinstance(session, session_module.AsyncSession)


@pytest.mark.asyncio
async def test_safe_close_outlives_caller_cancellation() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    closed = asyncio.Event()
    cleanup_done = asyncio.Event()

    class FakeSession:
        async def close(self) -> None:
            started.set()
            await release.wait()
            closed.set()

    async def run_cleanup() -> None:
        try:
            await session_module._safe_close(cast(session_module.AsyncSession, FakeSession()))
        finally:
            cleanup_done.set()

    async with asyncio.TaskGroup() as group:
        task = group.create_task(run_cleanup())
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not cleanup_done.is_set()
        release.set()

    assert closed.is_set()
    assert cleanup_done.is_set()


@pytest.mark.asyncio
async def test_safe_rollback_outlives_caller_cancellation() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    rolled_back = asyncio.Event()
    cleanup_done = asyncio.Event()

    class FakeSession:
        def in_transaction(self) -> bool:
            return True

        async def rollback(self) -> None:
            started.set()
            await release.wait()
            rolled_back.set()

    async def run_cleanup() -> None:
        try:
            await session_module._safe_rollback(cast(session_module.AsyncSession, FakeSession()))
        finally:
            cleanup_done.set()

    async with asyncio.TaskGroup() as group:
        task = group.create_task(run_cleanup())
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not cleanup_done.is_set()
        release.set()

    assert rolled_back.is_set()
    assert cleanup_done.is_set()


@pytest.mark.asyncio
async def test_relax_commit_durability_is_noop_for_sqlite_sessions() -> None:
    statements: list[str] = []
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=NullPool)

    @sa_event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany) -> None:  # type: ignore[no-untyped-def]
        del conn, cursor, parameters, context, executemany
        statements.append(statement)

    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            await session_module.relax_commit_durability(session)
            # The no-op must not even open a transaction: on SQLite the helper
            # returns before touching the connection.
            assert not session.in_transaction()
    finally:
        await engine.dispose()

    assert all("synchronous_commit" not in statement for statement in statements)


@pytest.mark.asyncio
async def test_relax_commit_durability_emits_set_local_for_postgresql_sessions() -> None:
    executed: list[str] = []

    class _FakeDialect:
        name = "postgresql"

    class _FakeBind:
        dialect = _FakeDialect()

    class _FakeSession:
        def get_bind(self) -> _FakeBind:
            return _FakeBind()

        async def execute(self, statement: object) -> None:
            executed.append(str(statement))

    await session_module.relax_commit_durability(cast(session_module.AsyncSession, _FakeSession()))

    assert executed == ["SET LOCAL synchronous_commit = off"]


@pytest.mark.asyncio
async def test_sqlite_long_write_watchdog_reports_the_holder(tmp_path, monkeypatch, caplog) -> None:
    """Issue #1682: a write transaction outliving the busy timeout is the
    holder that makes every other writer surface 'database is locked'. The
    watchdog must attribute it — duration, first/last write statement, task —
    when it finally ends, since the stall self-recovers."""
    monkeypatch.setattr(session_module, "_SQLITE_LONG_WRITE_TRANSACTION_WARN_SECONDS", 0.0)
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'watchdog.db'}",
        poolclass=NullPool,
        connect_args={"timeout": 5.0},
    )
    session_module._configure_sqlite_engine(engine.sync_engine, enable_wal=True)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        caplog.clear()

        factory = async_sessionmaker(engine, expire_on_commit=False)
        with caplog.at_level(logging.WARNING, logger=session_module.__name__):
            async with factory() as session:
                session.add(
                    Account(
                        id="acc-watchdog",
                        chatgpt_account_id="workspace-w",
                        email="watchdog@example.com",
                        plan_type="plus",
                        access_token_encrypted=b"a",
                        refresh_token_encrypted=b"r",
                        id_token_encrypted=b"i",
                        last_refresh=datetime(2025, 1, 1),
                        status=AccountStatus.ACTIVE,
                    )
                )
                await session.commit()

        records = [
            record
            for record in caplog.records
            if record.levelno == logging.WARNING and "sqlite_long_write_transaction" in record.getMessage()
        ]
        assert records, "the watchdog must report a write transaction over the threshold"
        message = records[0].getMessage()
        assert "outcome=commit" in message
        assert "INSERT INTO accounts" in message
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_long_write_watchdog_stays_silent_below_threshold_and_for_reads(tmp_path, caplog) -> None:
    """Fast writes and read-only transactions (which never take the writer
    slot in WAL) must not produce reports."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'quiet.db'}",
        poolclass=NullPool,
        connect_args={"timeout": 5.0},
    )
    session_module._configure_sqlite_engine(engine.sync_engine, enable_wal=True)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        caplog.clear()

        factory = async_sessionmaker(engine, expire_on_commit=False)
        with caplog.at_level(logging.WARNING, logger=session_module.__name__):
            async with factory() as session:
                (await session.execute(sa_text("SELECT count(*) FROM accounts"))).scalar_one()
                await session.commit()
            async with factory() as session:
                await session.execute(sa_text("DELETE FROM accounts"))
                await session.commit()

        assert not [
            record
            for record in caplog.records
            if record.levelno >= logging.WARNING and "sqlite_long_write_transaction" in record.getMessage()
        ]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_long_write_watchdog_does_not_blame_a_victim_waiting_for_the_lock(
    tmp_path, monkeypatch, caplog
) -> None:
    """A write statement can spend the whole busy timeout waiting for the slot
    and fail with 'database is locked'. That transaction never held the slot,
    so its rollback must not be reported as the holder — the clock starts only
    after the first write statement succeeds."""
    monkeypatch.setattr(session_module, "_SQLITE_LONG_WRITE_TRANSACTION_WARN_SECONDS", 0.2)
    db_path = tmp_path / "victim.db"
    holder_engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool, connect_args={"timeout": 5.0}
    )
    # Victim gets a short busy timeout so the test stays fast; install the
    # watchdog directly because the pragma configurer would override the
    # driver timeout with the production 30s busy_timeout.
    victim_engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool, connect_args={"timeout": 0.4}
    )
    session_module._install_sqlite_long_write_watchdog(victim_engine.sync_engine)
    try:
        async with holder_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        caplog.clear()

        holder_factory = async_sessionmaker(holder_engine, expire_on_commit=False)
        victim_factory = async_sessionmaker(victim_engine, expire_on_commit=False)
        with caplog.at_level(logging.WARNING, logger=session_module.__name__):
            async with holder_factory() as holder_session:
                # Journal mode: this write holds the database lock until commit.
                await holder_session.execute(sa_text("DELETE FROM accounts"))
                async with victim_factory() as victim_session:
                    with pytest.raises(Exception, match="database is locked"):
                        await victim_session.execute(sa_text("DELETE FROM accounts"))
                    await victim_session.rollback()
                await holder_session.commit()

        blamed = [
            record
            for record in caplog.records
            if record.levelno >= logging.WARNING and "sqlite_long_write_transaction" in record.getMessage()
        ]
        assert not blamed, "the victim's busy-timeout wait must not be reported as a held slot"
    finally:
        await victim_engine.dispose()
        await holder_engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_long_write_watchdog_includes_a_slow_transaction_end_in_the_hold(
    tmp_path, monkeypatch, caplog
) -> None:
    """ConnectionEvents.commit/rollback fire before the DBAPI call, and a
    wedged rollback is exactly the holder this watchdog hunts. The report is
    deferred to the first proof the transaction ended (next begin on the
    connection, or pool checkin), so the wedge itself is inside the measured
    hold."""
    monkeypatch.setattr(session_module, "_SQLITE_LONG_WRITE_TRANSACTION_WARN_SECONDS", 0.15)
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'slow-end.db'}",
        poolclass=NullPool,
        connect_args={"timeout": 5.0},
    )
    session_module._install_sqlite_long_write_watchdog(engine.sync_engine)

    # A commit whose DBAPI call itself stalls: the event fires, then the
    # "driver" spends longer than the threshold before the transaction is over.
    real_commit_events = []

    @sa_event.listens_for(engine.sync_engine, "commit")
    def _stall_after_mark(conn) -> None:
        # Runs after the watchdog's own commit listener marked the pending
        # report; the sleep stands in for a wedged DBAPI commit/rollback.
        real_commit_events.append(True)
        import time as _time

        _time.sleep(0.2)

    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        caplog.clear()

        factory = async_sessionmaker(engine, expire_on_commit=False)
        with caplog.at_level(logging.WARNING, logger=session_module.__name__):
            async with factory() as session:
                await session.execute(sa_text("DELETE FROM accounts"))
                await session.commit()

        records = [
            record
            for record in caplog.records
            if record.levelno == logging.WARNING and "sqlite_long_write_transaction" in record.getMessage()
        ]
        assert real_commit_events, "the stalling commit listener must have run"
        assert records, "a hold whose transaction end itself stalls must still be reported"
        assert "outcome=commit" in records[0].getMessage()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_long_write_watchdog_tracks_begin_immediate_holders(tmp_path, monkeypatch, caplog) -> None:
    """BEGIN IMMEDIATE acquires the writer slot with no DML at all (the
    accounts merge lock does exactly this), so a holder that never runs a
    write statement must still be attributed."""
    monkeypatch.setattr(session_module, "_SQLITE_LONG_WRITE_TRANSACTION_WARN_SECONDS", 0.0)
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'immediate.db'}",
        poolclass=NullPool,
        connect_args={"timeout": 5.0},
    )
    session_module._install_sqlite_long_write_watchdog(engine.sync_engine)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        caplog.clear()

        factory = async_sessionmaker(engine, expire_on_commit=False)
        with caplog.at_level(logging.WARNING, logger=session_module.__name__):
            async with factory() as session:
                await session.execute(sa_text("BEGIN IMMEDIATE"))
                (await session.execute(sa_text("SELECT count(*) FROM accounts"))).scalar_one()
                await session.commit()

        records = [
            record
            for record in caplog.records
            if record.levelno == logging.WARNING and "sqlite_long_write_transaction" in record.getMessage()
        ]
        assert records, "a BEGIN IMMEDIATE holder with no DML must still be attributed"
        assert "BEGIN IMMEDIATE" in records[0].getMessage()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_long_write_watchdog_reports_a_failed_commit_as_rollback(tmp_path, monkeypatch, caplog) -> None:
    """A commit whose DBAPI call raises is followed by a rollback; the report
    must not claim a durable commit that never happened."""
    monkeypatch.setattr(session_module, "_SQLITE_LONG_WRITE_TRANSACTION_WARN_SECONDS", 0.0)
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'failed-commit.db'}",
        poolclass=NullPool,
        connect_args={"timeout": 5.0},
    )
    session_module._install_sqlite_long_write_watchdog(engine.sync_engine)

    fail_next_commit = {"armed": False}

    from sqlalchemy.dialects.sqlite.aiosqlite import AsyncAdapt_aiosqlite_connection

    real_commit = AsyncAdapt_aiosqlite_connection.commit

    def failing_commit(self) -> None:
        if fail_next_commit["armed"]:
            fail_next_commit["armed"] = False
            raise RuntimeError("simulated DBAPI commit failure")
        real_commit(self)

    monkeypatch.setattr(AsyncAdapt_aiosqlite_connection, "commit", failing_commit)

    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        caplog.clear()

        factory = async_sessionmaker(engine, expire_on_commit=False)
        with caplog.at_level(logging.WARNING, logger=session_module.__name__):
            async with factory() as session:
                await session.execute(sa_text("DELETE FROM accounts"))
                fail_next_commit["armed"] = True
                with pytest.raises(Exception):
                    await session.commit()
                await session.rollback()

        records = [
            record
            for record in caplog.records
            if record.levelno == logging.WARNING and "sqlite_long_write_transaction" in record.getMessage()
        ]
        assert records
        assert "outcome=commit_failed_rollback" in records[0].getMessage()
        assert "outcome=commit " not in records[0].getMessage()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_shielded_bounded_returns_none_when_the_awaitable_finishes_in_time() -> None:
    async def _fast() -> str:
        return "done"

    assert await session_module._shielded_bounded(_fast(), 1.0) is None

    async def _boom() -> None:
        raise RuntimeError("teardown failed")

    with pytest.raises(RuntimeError, match="teardown failed"):
        await session_module._shielded_bounded(_boom(), 1.0)


@pytest.mark.asyncio
async def test_shielded_bounded_abandons_a_wedged_awaitable_at_the_deadline() -> None:
    release = asyncio.Event()

    async def _wedged() -> None:
        await release.wait()

    abandoned = await session_module._shielded_bounded(_wedged(), 0.05)
    assert abandoned is not None
    assert not abandoned.done(), "the wedged awaitable must be left running, not cancelled"
    release.set()
    await abandoned


@pytest.mark.asyncio
async def test_shielded_bounded_absorbs_caller_cancellation_like_the_unbounded_shield() -> None:
    """Teardown runs in ``finally`` blocks: the bound, not the caller's
    cancellation, must decide abandonment (matching ``_shielded`` + the
    swallow in ``_safe_rollback``/``_safe_close``)."""
    started = asyncio.Event()
    release = asyncio.Event()
    finished: list[bool] = []

    async def _work() -> None:
        started.set()
        await release.wait()
        finished.append(True)

    async def _caller() -> asyncio.Task[object] | None:
        return await session_module._shielded_bounded(_work(), 5.0)

    caller = asyncio.ensure_future(_caller())
    await started.wait()
    caller.cancel()
    await asyncio.sleep(0.05)
    assert not caller.done(), "cancellation must not abandon the shielded teardown"
    release.set()
    assert await caller is None
    assert finished, "the shielded work must run to completion despite the cancellation"


@pytest.mark.asyncio
async def test_close_session_reclaims_a_wedged_sqlite_rollback_so_other_writers_recover(
    tmp_path, monkeypatch, caplog
) -> None:
    """Issue #1682 part 2: a wedged rollback used to be awaited forever while
    the aiosqlite worker kept the single writer slot — a self-sustaining
    'database is locked' stall that starved leader election itself. The
    teardown must be bounded, and the bound alone is not enough: the wedged
    connection must be interrupted and invalidated so the writer slot is
    actually released and the connection is never handed out again."""
    monkeypatch.setattr(session_module, "_SQLITE_TEARDOWN_TIMEOUT_SECONDS", 0.2)
    db_path = tmp_path / "wedged-rollback.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        poolclass=NullPool,
        connect_args={"timeout": 5.0},
    )
    session_module._configure_sqlite_engine(engine.sync_engine, enable_wal=True)
    # An independent writer with a short busy timeout: if the reclaim fails to
    # release the writer slot, its INSERT surfaces 'database is locked' fast.
    other_writer = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        poolclass=NullPool,
        connect_args={"timeout": 1.0},
    )
    release_wedge = asyncio.Event()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        caplog.clear()

        factory = async_sessionmaker(engine, expire_on_commit=False)
        session = factory()
        # Take the writer slot with an uncommitted write.
        await session.execute(sa_text("DELETE FROM accounts"))

        held = session_module._session_sync_connections(session)
        assert held, "the open write transaction must expose its sync connection"
        sync_connection = held[0]
        driver = sync_connection.connection.driver_connection
        assert driver is not None

        # Wedge this connection's rollback (a stuck aiosqlite worker queues
        # the teardown behind itself exactly like this) and spy on interrupt.
        original_rollback = driver.rollback
        interrupted = asyncio.Event()
        original_interrupt = driver.interrupt

        async def _wedged_rollback() -> None:
            await release_wedge.wait()
            await original_rollback()

        # Delegate without changing the installed driver's shape: the reclaim
        # awaits ``interrupt()``'s result only when it is awaitable, so the
        # spy hands back exactly what the real aiosqlite method returns and
        # the production awaitable-handling is exercised against the installed
        # contract (a coroutine in the pinned aiosqlite) instead of a stand-in.
        def _spying_interrupt() -> object:
            interrupted.set()
            return original_interrupt()

        driver.rollback = _wedged_rollback
        driver.interrupt = _spying_interrupt

        with caplog.at_level(logging.INFO, logger=session_module.__name__):
            close_task = asyncio.ensure_future(session_module.close_session(session))
            done, _ = await asyncio.wait({close_task}, timeout=2.0)
            # RED on the pre-fix teardown: the shielded rollback was awaited
            # unboundedly, so close_session never returned.
            assert done, "close_session must be bounded when the sqlite rollback wedges"

            assert interrupted.is_set(), "the wedged driver must be interrupted to unstick its worker"
            assert sync_connection.invalidated, "the wedged connection must be invalidated, never reused"
            assert session.info.get(session_module._SQLITE_TEARDOWN_WEDGED_INFO_KEY) is True

            reclaim_logs = [
                record
                for record in caplog.records
                if record.levelno == logging.WARNING and "sqlite_wedged_teardown" in record.getMessage()
            ]
            assert reclaim_logs, "the reclaim must be reported with the watchdog's identifiers"
            message = reclaim_logs[0].getMessage()
            assert "phase=rollback" in message
            assert "DELETE FROM accounts" in message, "part 1 watchdog identifiers must attribute the holder"

            # The stall must not be self-sustaining: with the wedged rollback
            # still pending, another writer takes the slot immediately.
            async with other_writer.begin() as writer:
                await writer.execute(sa_text("DELETE FROM accounts"))

            # A wedged session is fenced: further teardown returns immediately
            # instead of driving the session concurrently with the abandoned
            # greenlet.
            await asyncio.wait_for(session_module.close_session(session), timeout=1.0)

            # Late completion: once the wedge resolves, the abandoned teardown
            # finishes and the session is closed for bookkeeping.
            release_wedge.set()
            for _ in range(100):
                if any("finished late" in record.getMessage() for record in caplog.records):
                    break
                await asyncio.sleep(0.02)
            assert any("finished late" in record.getMessage() for record in caplog.records), (
                "the abandoned teardown must be observed finishing late"
            )
            # The deferred bookkeeping close is owned until completion (drained
            # by close_db on shutdown), never fire-and-forget.
            pending_cleanup = tuple(session_module._wedged_teardown_cleanup_tasks)
            if pending_cleanup:
                await asyncio.wait_for(asyncio.gather(*pending_cleanup, return_exceptions=True), timeout=2.0)
            assert not session_module._wedged_teardown_cleanup_tasks, (
                "the deferred close must deregister itself once it completes"
            )
    finally:
        release_wedge.set()
        await asyncio.sleep(0.05)
        await engine.dispose()
        await other_writer.dispose()


@pytest.mark.asyncio
async def test_close_session_keeps_the_unbounded_shield_for_non_sqlite_sessions(monkeypatch) -> None:
    """PostgreSQL teardown semantics are untouched: a slow rollback/close far
    beyond the SQLite bound is still awaited to completion, never reclaimed."""
    monkeypatch.setattr(session_module, "_SQLITE_TEARDOWN_TIMEOUT_SECONDS", 0.01)

    class _FakeDialect:
        name = "postgresql"

    class _FakeBind:
        dialect = _FakeDialect()

    class _FakeSession:
        def __init__(self) -> None:
            self.info: dict[str, object] = {}
            self.rolled_back = False
            self.closed = False

        def get_bind(self) -> _FakeBind:
            return _FakeBind()

        def in_transaction(self) -> bool:
            return not self.rolled_back

        async def rollback(self) -> None:
            await asyncio.sleep(0.1)
            self.rolled_back = True

        async def close(self) -> None:
            await asyncio.sleep(0.1)
            self.closed = True

    fake = _FakeSession()
    await session_module.close_session(cast(session_module.AsyncSession, fake))

    assert fake.rolled_back, "the slow PostgreSQL rollback must be awaited to completion"
    assert fake.closed, "the slow PostgreSQL close must be awaited to completion"
    assert session_module._SQLITE_TEARDOWN_WEDGED_INFO_KEY not in fake.info


@pytest.mark.asyncio
async def test_close_session_bounds_a_wedged_sqlite_close_without_a_transaction(monkeypatch, caplog) -> None:
    """The close step can wedge on its own (connection release goes through
    the same aiosqlite worker); it must be bounded and fenced too."""
    monkeypatch.setattr(session_module, "_SQLITE_TEARDOWN_TIMEOUT_SECONDS", 0.05)
    release = asyncio.Event()

    class _FakeDialect:
        name = "sqlite"

    class _FakeUrl:
        database = "/tmp/wedged-close.db"
        query: dict[str, str] = {}

    class _FakeBind:
        dialect = _FakeDialect()
        url = _FakeUrl()

    class _FakeSyncSession:
        def get_transaction(self) -> None:
            return None

    class _FakeSession:
        def __init__(self) -> None:
            self.info: dict[str, object] = {}
            self.sync_session = _FakeSyncSession()

        def get_bind(self) -> _FakeBind:
            return _FakeBind()

        def in_transaction(self) -> bool:
            return False

        async def close(self) -> None:
            await release.wait()

    fake = _FakeSession()
    try:
        with caplog.at_level(logging.WARNING, logger=session_module.__name__):
            await asyncio.wait_for(session_module.close_session(cast(session_module.AsyncSession, fake)), timeout=2.0)

        assert fake.info.get(session_module._SQLITE_TEARDOWN_WEDGED_INFO_KEY) is True
        messages = [
            record.getMessage()
            for record in caplog.records
            if record.levelno == logging.WARNING and "sqlite_wedged_teardown" in record.getMessage()
        ]
        assert messages
        assert "phase=close" in messages[0]
    finally:
        release.set()
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_close_session_never_reclaims_the_shared_in_memory_sqlite_connection(monkeypatch) -> None:
    """In-memory SQLite shares one StaticPool connection with the whole
    process: invalidating it would destroy the entire database (the
    database-backends spec preserves shared in-memory state), and a single
    shared connection cannot starve other writers. The teardown must keep the
    unbounded shield there."""
    monkeypatch.setattr(session_module, "_SQLITE_TEARDOWN_TIMEOUT_SECONDS", 0.01)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        session = factory()
        await session.execute(sa_text("DELETE FROM accounts"))
        assert session_module._session_teardown_bound_seconds(session) is None

        held = session_module._session_sync_connections(session)
        assert held
        driver = held[0].connection.driver_connection
        assert driver is not None
        original_rollback = driver.rollback

        async def _slow_rollback() -> None:
            await asyncio.sleep(0.1)
            await original_rollback()

        driver.rollback = _slow_rollback
        await session_module.close_session(session)
        driver.rollback = original_rollback

        assert not held[0].invalidated, "the shared in-memory connection must never be invalidated"
        assert session_module._SQLITE_TEARDOWN_WEDGED_INFO_KEY not in session.info

        # The database survives: schema and connection are intact.
        verify = factory()
        (await verify.execute(sa_text("SELECT count(*) FROM accounts"))).scalar_one()
        await session_module.close_session(verify)
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "url_text",
    [
        "sqlite+aiosqlite:///:memory:",
        "sqlite+aiosqlite://",
        "sqlite+aiosqlite:///file:shared?mode=memory&cache=shared&uri=true",
        "sqlite+aiosqlite:///file::memory:?cache=shared&uri=true",
    ],
)
def test_session_teardown_bound_skips_every_in_memory_sqlite_url_form(url_text: str) -> None:
    """Every in-memory SQLite URL form must keep the unbounded teardown: the
    SQLite URI forms carry ``mode=memory`` in the parsed URL's query, not in
    ``url.database``, and a shared in-memory database reclaimed by invalidation
    would be destroyed for the whole process. URI forms count only with
    ``uri=true`` — that is what makes the dialect pass the string as a URI."""

    class _FakeDialect:
        name = "sqlite"

    class _FakeBind:
        dialect = _FakeDialect()
        url = make_url(url_text)

    class _FakeSession:
        def get_bind(self) -> _FakeBind:
            return _FakeBind()

    fake = _FakeSession()
    assert session_module._session_teardown_bound_seconds(cast(session_module.AsyncSession, fake)) is None


@pytest.mark.parametrize(
    "url_text",
    [
        "sqlite+aiosqlite:////data/store.db",
        "sqlite+aiosqlite:///file:/data/store.db?uri=true",
        # Without ``uri=true`` the dialect never enables SQLite URI mode: this
        # connects to a file literally named ``file:shared`` and must keep the
        # bounded teardown despite carrying ``mode=memory`` in the query.
        "sqlite+aiosqlite:///file:shared?mode=memory&cache=shared",
    ],
)
def test_session_teardown_bound_applies_to_file_backed_sqlite_url_forms(url_text: str) -> None:
    """File-backed SQLite (plain path, ``file:`` URI without ``mode=memory``,
    or a ``mode=memory`` query without ``uri=true``) is exactly the
    wedge-prone single-writer case and must stay bounded."""

    class _FakeDialect:
        name = "sqlite"

    class _FakeBind:
        dialect = _FakeDialect()
        url = make_url(url_text)

    class _FakeSession:
        def get_bind(self) -> _FakeBind:
            return _FakeBind()

    fake = _FakeSession()
    assert (
        session_module._session_teardown_bound_seconds(cast(session_module.AsyncSession, fake))
        == session_module._SQLITE_TEARDOWN_TIMEOUT_SECONDS
    )


@pytest.mark.asyncio
async def test_reclaim_interrupts_the_real_aiosqlite_driver_without_a_spy(tmp_path, caplog) -> None:
    """The reclaim invokes the driver's real ``interrupt()`` and awaits the
    result only when it is awaitable. Exercise the production path against the
    installed aiosqlite with no stand-in, so a driver signature change
    surfaces as a failure here instead of being swallowed by the reclaim's
    broad except (the failure is logged, and asserted absent)."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'interrupt-contract.db'}",
        poolclass=NullPool,
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        session = factory()
        await session.execute(sa_text("DELETE FROM accounts"))
        held = session_module._session_sync_connections(session)
        assert held, "the open write transaction must expose its sync connection"

        async def _already_finished_teardown() -> None:
            return None

        abandoned = asyncio.ensure_future(_already_finished_teardown())
        with caplog.at_level(logging.DEBUG, logger=session_module.__name__):
            await session_module._reclaim_wedged_sqlite_session(session, abandoned, held, phase="rollback")

        assert not any(
            "Interrupting a wedged SQLite connection failed" in record.getMessage() for record in caplog.records
        ), "the installed aiosqlite interrupt() contract must be handled without error"
        assert held[0].invalidated, "the reclaim must still invalidate the connection"

        # Drain the bookkeeping the reclaim registered so no task outlives
        # the test (mirrors the close_db drain).
        for _ in range(100):
            pending = tuple(session_module._wedged_teardown_cleanup_tasks)
            if not pending:
                break
            await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=2.0)
            await asyncio.sleep(0)
        assert not session_module._wedged_teardown_cleanup_tasks
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_close_db_drains_a_pending_reclaimed_rollback_and_its_bookkeeping_close(
    tmp_path, monkeypatch, caplog
) -> None:
    """A rollback reclaimed as wedged can still be pending when close_db runs.
    The abandoned task is registered in the teardown registry immediately, so
    close_db must wait for it — and for the bookkeeping close it schedules only
    after any one-time snapshot — instead of returning while the event loop
    still has pending teardown tasks."""
    monkeypatch.setattr(session_module, "_SQLITE_TEARDOWN_TIMEOUT_SECONDS", 0.5)
    db_path = tmp_path / "close-db-drain.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        poolclass=NullPool,
        connect_args={"timeout": 5.0},
    )
    session_module._configure_sqlite_engine(engine.sync_engine, enable_wal=True)
    release_wedge = asyncio.Event()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        factory = async_sessionmaker(engine, expire_on_commit=False)
        session = factory()
        await session.execute(sa_text("DELETE FROM accounts"))

        held = session_module._session_sync_connections(session)
        assert held
        driver = held[0].connection.driver_connection
        assert driver is not None
        original_rollback = driver.rollback

        async def _wedged_rollback() -> None:
            await release_wedge.wait()
            await original_rollback()

        driver.rollback = _wedged_rollback

        with caplog.at_level(logging.INFO, logger=session_module.__name__):
            await asyncio.wait_for(session_module.close_session(session), timeout=5.0)
            abandoned_pending = [task for task in session_module._wedged_teardown_cleanup_tasks if not task.done()]
            # RED pre-fix: the reclaim only registered the deferred bookkeeping
            # close (which does not exist yet), never the abandoned rollback.
            assert abandoned_pending, "the reclaimed rollback must be registered while still pending"

            async def _release_soon() -> None:
                await asyncio.sleep(0.05)
                release_wedge.set()

            releaser = asyncio.ensure_future(_release_soon())
            assert await asyncio.wait_for(session_module.close_db(), timeout=5.0) is True
            # RED pre-fix: close_db saw an empty registry and returned
            # immediately, before the wedge was even released.
            assert release_wedge.is_set(), "close_db must drain the pending reclaimed rollback"
            assert all(task.done() for task in abandoned_pending), (
                "close_db must wait for the abandoned rollback itself"
            )
            assert not session_module._wedged_teardown_cleanup_tasks, (
                "close_db must also drain the bookkeeping close scheduled after its first snapshot"
            )
            await releaser

        assert any("finished late" in record.getMessage() for record in caplog.records)
    finally:
        release_wedge.set()
        await asyncio.sleep(0.05)
        await engine.dispose()


@pytest.mark.asyncio
async def test_close_db_bounds_the_wedged_teardown_drain(monkeypatch, caplog) -> None:
    """A teardown that stays wedged despite the reclaim (the interrupt is
    best-effort) must not wedge shutdown too: the registry drain is explicitly
    bounded and abandons whatever remains after the deadline."""
    monkeypatch.setattr(session_module, "_SQLITE_TEARDOWN_TIMEOUT_SECONDS", 0.05)
    never = asyncio.Event()
    stuck: asyncio.Task[bool] = asyncio.ensure_future(never.wait())
    session_module._wedged_teardown_cleanup_tasks.add(stuck)
    try:
        with caplog.at_level(logging.WARNING, logger=session_module.__name__):
            assert await asyncio.wait_for(session_module.close_db(), timeout=2.0) is False
        assert any("still-pending wedged-teardown" in record.getMessage() for record in caplog.records), (
            "the bounded drain must report what it abandoned"
        )
        assert stuck in session_module._wedged_teardown_cleanup_tasks
    finally:
        session_module._wedged_teardown_cleanup_tasks.discard(stuck)
        never.set()
        await stuck


@pytest.mark.asyncio
async def test_close_db_drains_teardown_registered_during_engine_disposal(monkeypatch) -> None:
    """A disposal callback registered after the initial snapshot still gates clean."""
    monkeypatch.setattr(session_module, "_SQLITE_TEARDOWN_TIMEOUT_SECONDS", 0.5)
    release_wedge = asyncio.Event()
    late_task: asyncio.Task[bool] | None = None

    class _EngineThatRegistersLateTeardown:
        async def dispose(self) -> None:
            nonlocal late_task
            late_task = asyncio.create_task(release_wedge.wait(), name="late-disposal-teardown")
            session_module._wedged_teardown_cleanup_tasks.add(late_task)
            late_task.add_done_callback(session_module._wedged_teardown_cleanup_tasks.discard)

    monkeypatch.setattr(session_module, "engine", _EngineThatRegistersLateTeardown())
    monkeypatch.setattr(session_module, "_background_engine", None)

    async def _release_soon() -> None:
        await asyncio.sleep(0.05)
        release_wedge.set()

    releaser = asyncio.create_task(_release_soon())
    try:
        assert await asyncio.wait_for(session_module.close_db(), timeout=5.0) is True
        assert release_wedge.is_set(), "close_db must drain teardown registered by engine disposal"
        assert late_task is not None and late_task.done()
        assert not session_module._wedged_teardown_cleanup_tasks
    finally:
        release_wedge.set()
        await releaser
        if late_task is not None:
            await late_task


def _stub_head_migration_state(monkeypatch) -> None:
    monkeypatch.setattr(
        session_module,
        "_load_migration_entrypoints",
        lambda: (
            lambda _: _FakeMigrationState(
                current_revision="head",
                head_revision="head",
                has_alembic_version_table=True,
                has_legacy_migrations_table=False,
                needs_upgrade=False,
            ),
            lambda _: (_ for _ in ()).throw(AssertionError("startup migrations should stay disabled")),
            lambda _: (),
        ),
    )


@pytest.mark.asyncio
async def test_init_db_skips_startup_check_after_recorded_clean_shutdown(monkeypatch, tmp_path, caplog) -> None:
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")
    write_sqlite_runstate(db_path, SqliteRunState.CLEAN)

    def _check(_: Path, *, mode: SqliteIntegrityCheckMode = SqliteIntegrityCheckMode.FULL) -> IntegrityCheck:
        raise AssertionError("a cleanly closed store must not pay for the whole-file scan")

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url=f"sqlite+aiosqlite:///{db_path}",
            database_migrate_on_startup=False,
        ),
    )
    monkeypatch.setattr(session_module, "check_sqlite_integrity", _check)
    _stub_head_migration_state(monkeypatch)

    with caplog.at_level(logging.INFO, logger=session_module.__name__):
        await session_module.init_db()

    assert read_sqlite_runstate(db_path) is SqliteRunState.RUNNING
    assert "Skipping SQLite startup quick_check after a recorded clean shutdown" in caplog.text


@pytest.mark.asyncio
async def test_init_db_runs_startup_check_when_running_state_write_fails(monkeypatch, tmp_path, caplog) -> None:
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")
    write_sqlite_runstate(db_path, SqliteRunState.CLEAN)
    seen: list[SqliteIntegrityCheckMode] = []

    monkeypatch.setattr(session_module, "_mark_sqlite_running", lambda _: False)

    def _check(path: Path, *, mode: SqliteIntegrityCheckMode = SqliteIntegrityCheckMode.FULL) -> IntegrityCheck:
        assert path == db_path
        seen.append(mode)
        return IntegrityCheck(ok=True, details=None)

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url=f"sqlite+aiosqlite:///{db_path}",
            database_migrate_on_startup=False,
        ),
    )
    monkeypatch.setattr(session_module, "check_sqlite_integrity", _check)
    _stub_head_migration_state(monkeypatch)

    with caplog.at_level(logging.INFO, logger=session_module.__name__):
        await session_module.init_db()

    assert seen == [SqliteIntegrityCheckMode.QUICK]
    assert "Running SQLite startup quick_check" in caplog.text
    assert "SQLite startup quick_check passed in" in caplog.text


@pytest.mark.asyncio
async def test_init_db_aborts_after_check_when_running_invalidation_is_not_durable(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")
    write_sqlite_runstate(db_path, SqliteRunState.CLEAN)
    seen: list[SqliteIntegrityCheckMode] = []
    migration_loaded = False

    def _raise_unconfirmed_invalidation(_: Path, __: SqliteRunState) -> bool:
        raise SqliteRunStateDurabilityError("could not persist removal of failed SQLite run-state files")

    def _load_entrypoints() -> tuple[object, object, object]:
        nonlocal migration_loaded
        migration_loaded = True
        return (
            lambda _: _FakeMigrationState(
                current_revision="head",
                head_revision="head",
                has_alembic_version_table=True,
                has_legacy_migrations_table=False,
                needs_upgrade=False,
            ),
            lambda _: (_ for _ in ()).throw(AssertionError("startup migrations must not run")),
            lambda _: (),
        )

    def _check(path: Path, *, mode: SqliteIntegrityCheckMode = SqliteIntegrityCheckMode.FULL) -> IntegrityCheck:
        assert path == db_path
        seen.append(mode)
        return IntegrityCheck(ok=True, details=None)

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url=f"sqlite+aiosqlite:///{db_path}",
            database_migrate_on_startup=False,
        ),
    )
    monkeypatch.setattr(session_module, "write_sqlite_runstate", _raise_unconfirmed_invalidation)
    monkeypatch.setattr(session_module, "check_sqlite_integrity", _check)
    monkeypatch.setattr(session_module, "_load_migration_entrypoints", _load_entrypoints)

    with pytest.raises(SqliteRunStateDurabilityError, match="persist removal"):
        await session_module.init_db()

    assert seen == [SqliteIntegrityCheckMode.QUICK]
    assert migration_loaded is False
    replacement = acquire_sqlite_runstate_lock(db_path)
    release_sqlite_runstate_lock(replacement)


@pytest.mark.asyncio
async def test_init_db_runs_startup_check_when_database_replaced_before_running_fence(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"original")
    write_sqlite_runstate(db_path, SqliteRunState.CLEAN)
    replacement = tmp_path / "replacement.db"
    replacement.write_bytes(b"replacement")
    seen: list[SqliteIntegrityCheckMode] = []
    real_mark_running = session_module._mark_sqlite_running

    def _replace_before_mark(path: Path) -> bool:
        replacement.replace(path)
        return real_mark_running(path)

    def _check(path: Path, *, mode: SqliteIntegrityCheckMode = SqliteIntegrityCheckMode.FULL) -> IntegrityCheck:
        assert path == db_path
        seen.append(mode)
        return IntegrityCheck(ok=True, details=None)

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url=f"sqlite+aiosqlite:///{db_path}",
            database_migrate_on_startup=False,
        ),
    )
    monkeypatch.setattr(session_module, "_mark_sqlite_running", _replace_before_mark)
    monkeypatch.setattr(session_module, "check_sqlite_integrity", _check)
    _stub_head_migration_state(monkeypatch)

    await session_module.init_db()

    assert seen == [SqliteIntegrityCheckMode.QUICK]
    assert read_sqlite_runstate(db_path) is SqliteRunState.RUNNING


@pytest.mark.asyncio
async def test_init_db_runs_startup_check_when_database_replaced_after_running_fence(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"original")
    write_sqlite_runstate(db_path, SqliteRunState.CLEAN)
    replacement = tmp_path / "replacement.db"
    replacement.write_bytes(b"replacement")
    seen: list[SqliteIntegrityCheckMode] = []
    real_mark_running = session_module._mark_sqlite_running

    def _replace_after_mark(path: Path) -> bool:
        recorded = real_mark_running(path)
        replacement.replace(path)
        return recorded

    def _check(path: Path, *, mode: SqliteIntegrityCheckMode = SqliteIntegrityCheckMode.FULL) -> IntegrityCheck:
        assert path == db_path
        seen.append(mode)
        return IntegrityCheck(ok=True, details=None)

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url=f"sqlite+aiosqlite:///{db_path}",
            database_migrate_on_startup=False,
        ),
    )
    monkeypatch.setattr(session_module, "_mark_sqlite_running", _replace_after_mark)
    monkeypatch.setattr(session_module, "check_sqlite_integrity", _check)
    _stub_head_migration_state(monkeypatch)

    await session_module.init_db()

    assert seen == [SqliteIntegrityCheckMode.QUICK]
    assert read_sqlite_runstate(db_path) is SqliteRunState.RUNNING


@pytest.mark.asyncio
async def test_init_db_runs_startup_check_when_database_replaced_before_decision(
    monkeypatch,
    tmp_path,
) -> None:
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"original")
    write_sqlite_runstate(db_path, SqliteRunState.CLEAN)
    replacement = tmp_path / "replacement.db"
    replacement.write_bytes(b"replacement")
    seen: list[SqliteIntegrityCheckMode] = []
    real_check_required = session_module._sqlite_startup_check_required

    def _replace_before_decision(path: Path, **kwargs: Any) -> bool:
        replacement.replace(path)
        return real_check_required(path, **kwargs)

    def _check(path: Path, *, mode: SqliteIntegrityCheckMode = SqliteIntegrityCheckMode.FULL) -> IntegrityCheck:
        assert path == db_path
        seen.append(mode)
        return IntegrityCheck(ok=True, details=None)

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url=f"sqlite+aiosqlite:///{db_path}",
            database_migrate_on_startup=False,
        ),
    )
    monkeypatch.setattr(session_module, "_sqlite_startup_check_required", _replace_before_decision)
    monkeypatch.setattr(session_module, "check_sqlite_integrity", _check)
    _stub_head_migration_state(monkeypatch)

    await session_module.init_db()

    assert seen == [SqliteIntegrityCheckMode.QUICK]
    assert read_sqlite_runstate(db_path) is SqliteRunState.RUNNING


@pytest.mark.parametrize(
    ("running_identity", "current_identity"),
    [
        pytest.param(
            None,
            SqliteFileIdentity(dev=1, ino=1, size=1, mtime_ns=1, ctime_ns=1),
            id="running-identity-missing",
        ),
        pytest.param(
            SqliteFileIdentity(dev=1, ino=1, size=1, mtime_ns=1, ctime_ns=1),
            None,
            id="current-identity-unavailable",
        ),
    ],
)
def test_sqlite_startup_check_requires_non_null_identities_for_a_clean_skip(
    monkeypatch,
    tmp_path,
    running_identity,
    current_identity,
) -> None:
    db_path = tmp_path / "store.db"
    monkeypatch.setattr(session_module, "_sqlite_file_identity", lambda _: current_identity)

    assert (
        session_module._sqlite_startup_check_required(
            db_path,
            mode=SqliteIntegrityCheckMode.QUICK,
            previous_state=SqliteRunState.CLEAN,
            running_recorded=True,
            running_identity=running_identity,
        )
        is True
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "recorded_state",
    [
        pytest.param(None, id="no-sidecar-upgrade-or-first-run"),
        pytest.param(SqliteRunState.RUNNING, id="previous-process-did-not-finish"),
    ],
)
async def test_init_db_runs_startup_check_when_shutdown_was_not_clean(monkeypatch, tmp_path, recorded_state) -> None:
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")
    if recorded_state is not None:
        write_sqlite_runstate(db_path, recorded_state)
    seen: list[SqliteIntegrityCheckMode] = []

    def _check(path: Path, *, mode: SqliteIntegrityCheckMode = SqliteIntegrityCheckMode.FULL) -> IntegrityCheck:
        assert path == db_path
        seen.append(mode)
        return IntegrityCheck(ok=True, details=None)

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url=f"sqlite+aiosqlite:///{db_path}",
            database_migrate_on_startup=False,
        ),
    )
    monkeypatch.setattr(session_module, "check_sqlite_integrity", _check)
    _stub_head_migration_state(monkeypatch)

    await session_module.init_db()

    assert seen == [SqliteIntegrityCheckMode.QUICK]
    assert read_sqlite_runstate(db_path) is SqliteRunState.RUNNING


@pytest.mark.asyncio
async def test_init_db_records_running_state_even_when_check_is_disabled(monkeypatch, tmp_path) -> None:
    """Re-enabling the check must not trust a state this process never wrote."""
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")
    write_sqlite_runstate(db_path, SqliteRunState.CLEAN)

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url=f"sqlite+aiosqlite:///{db_path}",
            database_migrate_on_startup=False,
            database_sqlite_startup_check_mode="off",
        ),
    )
    monkeypatch.setattr(
        session_module,
        "check_sqlite_integrity",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("check is disabled")),
    )
    _stub_head_migration_state(monkeypatch)

    await session_module.init_db()

    assert read_sqlite_runstate(db_path) is SqliteRunState.RUNNING


@pytest.mark.asyncio
async def test_init_db_leaves_state_unclean_when_the_startup_check_fails(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url=f"sqlite+aiosqlite:///{db_path}",
            database_migrate_on_startup=False,
        ),
    )
    monkeypatch.setattr(
        session_module,
        "check_sqlite_integrity",
        lambda *_args, **_kwargs: IntegrityCheck(ok=False, details="database disk image is malformed"),
    )
    _stub_head_migration_state(monkeypatch)

    with pytest.raises(RuntimeError, match="quick_check failed"):
        await session_module.init_db()

    assert read_sqlite_runstate(db_path) is SqliteRunState.RUNNING


@pytest.mark.asyncio
async def test_init_db_does_not_trust_clean_sidecar_after_failed_check_on_retry(monkeypatch, tmp_path) -> None:
    """A failed startup must leave RUNNING so a retry cannot skip its check."""
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")
    write_sqlite_runstate(db_path, SqliteRunState.CLEAN)
    check_attempts = 0
    required_decisions = 0
    real_check_required = session_module._sqlite_startup_check_required

    def _check(_: Path, *, mode: SqliteIntegrityCheckMode = SqliteIntegrityCheckMode.FULL) -> IntegrityCheck:
        nonlocal check_attempts
        check_attempts += 1
        assert mode is SqliteIntegrityCheckMode.QUICK
        assert read_sqlite_runstate(db_path) is SqliteRunState.RUNNING
        return IntegrityCheck(ok=False, details="database disk image is malformed")

    def _check_required(path: Path, **kwargs: Any) -> bool:
        nonlocal required_decisions
        required_decisions += 1
        if required_decisions == 1:
            assert read_sqlite_runstate(path) is SqliteRunState.RUNNING
            return True
        return real_check_required(path, **kwargs)

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url=f"sqlite+aiosqlite:///{db_path}",
            database_migrate_on_startup=False,
        ),
    )
    monkeypatch.setattr(session_module, "check_sqlite_integrity", _check)
    monkeypatch.setattr(session_module, "_sqlite_startup_check_required", _check_required)
    _stub_head_migration_state(monkeypatch)

    for _ in range(2):
        with pytest.raises(RuntimeError, match="quick_check failed"):
            await session_module.init_db()
        assert read_sqlite_runstate(db_path) is SqliteRunState.RUNNING

    assert check_attempts == 2


def test_mark_sqlite_shutdown_clean_records_a_clean_close(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")
    write_sqlite_runstate(db_path, SqliteRunState.RUNNING)

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(database_url=f"sqlite+aiosqlite:///{db_path}"),
    )

    session_module._acquire_sqlite_lifetime_lock(db_path)
    session_module.mark_sqlite_shutdown_clean()

    assert read_sqlite_runstate(db_path) is SqliteRunState.CLEAN


def test_mark_sqlite_shutdown_clean_releases_the_lifetime_lock(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")
    write_sqlite_runstate(db_path, SqliteRunState.RUNNING)

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(database_url=f"sqlite+aiosqlite:///{db_path}"),
    )

    session_module._acquire_sqlite_lifetime_lock(db_path)
    session_module.mark_sqlite_shutdown_clean()

    assert session_module._sqlite_lifetime_lock is None
    replacement = acquire_sqlite_runstate_lock(db_path)
    release_sqlite_runstate_lock(replacement)


@pytest.mark.asyncio
async def test_init_db_fails_closed_when_another_process_holds_the_lifetime_lock(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")
    held = acquire_sqlite_runstate_lock(db_path)
    try:
        monkeypatch.setattr(
            session_module,
            "_settings",
            _FakeSettings(
                database_url=f"sqlite+aiosqlite:///{db_path}",
                database_migrate_on_startup=False,
            ),
        )

        with pytest.raises(RuntimeError, match="SQLite lifetime lock"):
            await session_module.init_db()
    finally:
        release_sqlite_runstate_lock(held)


def test_mark_sqlite_shutdown_clean_is_inert_for_non_sqlite_backends(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(database_url="postgresql+asyncpg://user@localhost/codexlb"),
    )

    session_module.mark_sqlite_shutdown_clean()

    assert not list(tmp_path.iterdir())
