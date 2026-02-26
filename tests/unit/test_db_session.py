from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import app.db.session as session_module


@dataclass(slots=True)
class _FakeSettings:
    database_url: str
    database_migrate_on_startup: bool = True
    database_sqlite_pre_migrate_backup_enabled: bool = False
    database_sqlite_pre_migrate_backup_max_files: int = 5
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

    def _load_entrypoints() -> tuple[object, object]:
        return _inspect_migration_state, _run_startup_migrations

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
