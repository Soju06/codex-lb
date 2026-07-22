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
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.db.session as session_module
from app.db.models import Account, AccountStatus, Base
from app.db.sqlite_utils import IntegrityCheck, SqliteIntegrityCheckMode


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

    assert connect_args == {"server_settings": {"timezone": "UTC"}}


def test_postgres_connect_args_pin_utc_and_keep_test_db_url_tuning(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_TEST_DATABASE_URL", "1")

    connect_args = session_module._postgres_async_connect_args("postgresql+asyncpg://u:p@h/db")

    assert connect_args == {
        "server_settings": {"timezone": "UTC"},
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

    assert kwargs["connect_args"] == {"server_settings": {"timezone": "UTC"}}


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
        assert cast(Any, pool).size() == 15

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
