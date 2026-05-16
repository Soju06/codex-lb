from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy.pool import NullPool

import app.db.session as session_module
from app.db.sqlite_utils import IntegrityCheck, SqliteIntegrityCheckMode


@dataclass(slots=True)
class _FakeSettings:
    database_url: str
    database_pool_size: int = 15
    database_max_overflow: int = 10
    database_background_pool_size: int | None = None
    database_background_max_overflow: int | None = None
    database_pool_timeout_seconds: float = 30.0
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


def test_background_pool_defaults_to_main_pool_settings(monkeypatch) -> None:
    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url="sqlite+aiosqlite:///:memory:",
            database_pool_size=15,
            database_max_overflow=10,
        ),
    )

    assert session_module._database_pool_size(background=True) == 15
    assert session_module._database_max_overflow(background=True) == 10


def test_background_pool_settings_can_override_main_pool(monkeypatch) -> None:
    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url="sqlite+aiosqlite:///:memory:",
            database_pool_size=15,
            database_max_overflow=10,
            database_background_pool_size=4,
            database_background_max_overflow=1,
        ),
    )

    assert session_module._database_pool_size(background=True) == 4
    assert session_module._database_max_overflow(background=True) == 1
    assert session_module._database_pool_size(background=False) == 15
    assert session_module._database_max_overflow(background=False) == 10


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
async def test_init_background_db_uses_main_pool_size_for_postgres_by_default() -> None:
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
