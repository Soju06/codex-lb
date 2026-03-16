from __future__ import annotations

import logging
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
    database_migration_url: str | None = None
    database_migrate_on_startup: bool = True
    database_migrations_fail_fast: bool = False


@dataclass(slots=True)
class _FakeBootstrap:
    stamped_revision: str | None = None
    legacy_row_count: int = 0


@dataclass(slots=True)
class _FakeMigrationRunResult:
    current_revision: str | None = "head"
    bootstrap: _FakeBootstrap = field(default_factory=_FakeBootstrap)


def test_import_session_requires_database_url() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["CODEX_LB_DATABASE_URL"] = ""
    env["CODEX_LB_DATABASE_MIGRATION_URL"] = ""

    result = subprocess.run(
        [sys.executable, "-c", "import app.db.session"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "database_url" in (result.stderr or result.stdout)


def test_runtime_database_url_normalizes_asyncpg_ssl_query_params() -> None:
    url = (
        "postgresql+asyncpg://user:pass@host/db?sslmode=require&channel_binding=require&application_name=codex-lb"
    )

    normalized = session_module._runtime_database_url(url)

    assert "ssl=require" in normalized
    assert "sslmode=" not in normalized
    assert "channel_binding=" not in normalized
    assert "application_name=codex-lb" in normalized


def test_import_session_with_postgres_url_does_not_error() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["CODEX_LB_DATABASE_URL"] = "postgresql+asyncpg://codex_lb:codex_lb@127.0.0.1:5432/codex_lb"
    env["CODEX_LB_DATABASE_MIGRATION_URL"] = env["CODEX_LB_DATABASE_URL"]

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
async def test_init_db_requires_migration_url_when_startup_migrations_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url="postgresql+asyncpg://codex_lb:codex_lb@127.0.0.1:5432/codex_lb",
            database_migration_url=None,
            database_migrate_on_startup=True,
        ),
    )

    with pytest.raises(RuntimeError, match="CODEX_LB_DATABASE_MIGRATION_URL is required"):
        await session_module.init_db()


@pytest.mark.asyncio
async def test_init_db_fails_when_migration_module_is_missing_even_with_fail_fast_disabled(monkeypatch) -> None:
    def _raise_missing_migration() -> tuple[object, object]:
        raise ModuleNotFoundError("No module named 'app.db.migrate'", name="app.db.migrate")

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url="postgresql+asyncpg://codex_lb:codex_lb@127.0.0.1:5432/codex_lb",
            database_migration_url="postgresql+asyncpg://codex_lb:codex_lb@127.0.0.1:5432/codex_lb",
            database_migrations_fail_fast=False,
        ),
    )
    monkeypatch.setattr(session_module, "_load_migration_entrypoints", _raise_missing_migration)

    with pytest.raises(RuntimeError, match=r"app\.db\.migrate is unavailable"):
        await session_module.init_db()


@pytest.mark.asyncio
async def test_init_db_fails_when_migration_entrypoint_is_invalid_even_with_fail_fast_disabled(monkeypatch) -> None:
    def _raise_invalid_migration() -> tuple[object, object]:
        raise ImportError("cannot import name 'run_startup_migrations' from 'app.db.migrate'")

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url="postgresql+asyncpg://codex_lb:codex_lb@127.0.0.1:5432/codex_lb",
            database_migration_url="postgresql+asyncpg://codex_lb:codex_lb@127.0.0.1:5432/codex_lb",
            database_migrations_fail_fast=False,
        ),
    )
    monkeypatch.setattr(session_module, "_load_migration_entrypoints", _raise_invalid_migration)

    with pytest.raises(RuntimeError, match=r"app\.db\.migrate is invalid"):
        await session_module.init_db()


@pytest.mark.asyncio
async def test_init_db_fails_fast_on_post_migration_schema_drift(monkeypatch) -> None:
    seen: list[str] = []

    async def _run_startup_migrations(url: str) -> _FakeMigrationRunResult:
        seen.append(url)
        return _FakeMigrationRunResult()

    def _check_schema_drift(url: str) -> tuple[str, ...]:
        seen.append(url)
        return ("('add_table', 'additional_usage_history')",)

    def _load_entrypoints() -> tuple[object, object]:
        return _run_startup_migrations, _check_schema_drift

    migration_url = "postgresql+asyncpg://migrate:migrate@127.0.0.1:5432/codex_lb"
    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url="postgresql+asyncpg://runtime:runtime@127.0.0.1:5432/codex_lb",
            database_migration_url=migration_url,
            database_migrations_fail_fast=True,
        ),
    )
    monkeypatch.setattr(session_module, "_load_migration_entrypoints", _load_entrypoints)

    with pytest.raises(RuntimeError, match="Schema drift detected after startup migrations"):
        await session_module.init_db()

    assert seen == [migration_url, migration_url]


@pytest.mark.asyncio
async def test_init_db_logs_post_migration_schema_drift_when_fail_fast_disabled(monkeypatch, caplog) -> None:
    async def _run_startup_migrations(_: str) -> _FakeMigrationRunResult:
        return _FakeMigrationRunResult()

    def _check_schema_drift(_: str) -> tuple[str, ...]:
        return ("('missing_index', 'request_logs', 'idx_logs_requested_at_id')",)

    def _load_entrypoints() -> tuple[object, object]:
        return _run_startup_migrations, _check_schema_drift

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url="postgresql+asyncpg://runtime:runtime@127.0.0.1:5432/codex_lb",
            database_migration_url="postgresql+asyncpg://migrate:migrate@127.0.0.1:5432/codex_lb",
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
async def test_init_db_skips_startup_migration_when_disabled(monkeypatch) -> None:
    def _load_entrypoints() -> tuple[object, object]:
        raise AssertionError("migration entrypoints should not load when startup migrations are disabled")

    monkeypatch.setattr(
        session_module,
        "_settings",
        _FakeSettings(
            database_url="postgresql+asyncpg://runtime:runtime@127.0.0.1:5432/codex_lb",
            database_migration_url=None,
            database_migrate_on_startup=False,
        ),
    )
    monkeypatch.setattr(session_module, "_load_migration_entrypoints", _load_entrypoints)

    await session_module.init_db()
