from __future__ import annotations

import asyncio
import logging
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import app.db.migrate as migrate_module
from app.core.config.settings import get_settings
from app.db.migrate import MigrationBootstrapError, inspect_migration_state, run_upgrade
from app.db.migration_lock import migration_lock
from app.db.migration_url import to_sync_database_url

pytestmark = pytest.mark.integration

_DATABASE_URL = get_settings().database_url
_HEAD_REVISION = inspect_migration_state(_DATABASE_URL).head_revision
_FUTURE_REVISION = "20991231_000000_revision_from_a_newer_build"
_REPO_ROOT = Path(migrate_module.__file__).resolve().parents[2]
_LOCK_HOLD_TIMEOUT_SECONDS = 60.0


def _is_postgresql_database_url(url: str) -> bool:
    return url.startswith("postgresql+")


def _sqlite_alembic_revisions(db_path: Path) -> list[str]:
    with sqlite3.connect(str(db_path)) as connection:
        rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    return sorted(str(row[0]) for row in rows)


def test_concurrent_upgrades_on_fresh_sqlite_database_apply_head_exactly_once(tmp_path: Path) -> None:
    db_path = tmp_path / "race.sqlite"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    barrier = threading.Barrier(2)

    def _upgrade() -> migrate_module.MigrationRunResult:
        # Align both "replicas" right before run_upgrade so they race the full
        # inspect -> bootstrap -> upgrade sequence, as two containers booting
        # simultaneously against one shared database file would.
        barrier.wait(timeout=30)
        return run_upgrade(db_url, "head", bootstrap_legacy=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_upgrade) for _ in range(2)]
        results = [future.result(timeout=300) for future in futures]

    assert [result.current_revision for result in results] == [_HEAD_REVISION, _HEAD_REVISION]
    assert _sqlite_alembic_revisions(db_path) == [_HEAD_REVISION]


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _is_postgresql_database_url(_DATABASE_URL),
    reason="PostgreSQL-only concurrent migration test",
)
async def test_concurrent_upgrades_on_fresh_postgresql_database_apply_head_exactly_once(db_setup) -> None:
    from app.db.session import SessionLocal

    async with SessionLocal() as session:
        await session.execute(text("DROP SCHEMA public CASCADE"))
        await session.execute(text("CREATE SCHEMA public"))
        await session.commit()

    barrier = threading.Barrier(2)

    def _upgrade() -> migrate_module.MigrationRunResult:
        barrier.wait(timeout=30)
        return run_upgrade(_DATABASE_URL, "head", bootstrap_legacy=True)

    results = await asyncio.gather(asyncio.to_thread(_upgrade), asyncio.to_thread(_upgrade))
    assert [result.current_revision for result in results] == [_HEAD_REVISION, _HEAD_REVISION]

    async with SessionLocal() as session:
        revision_rows = await session.execute(text("SELECT version_num FROM alembic_version"))
        revisions = sorted(str(row[0]) for row in revision_rows.fetchall())
        assert revisions == [_HEAD_REVISION]


def test_run_upgrade_waits_for_lock_holder_then_skips_when_already_at_head(tmp_path: Path, caplog) -> None:
    db_path = tmp_path / "skip.sqlite"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    run_upgrade(db_url, "head", bootstrap_legacy=True)

    caplog.set_level(logging.INFO, logger="app.db.migrate")
    sync_url = to_sync_database_url(db_url)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with migration_lock(sync_url, timeout_seconds=_LOCK_HOLD_TIMEOUT_SECONDS):
            future = pool.submit(
                run_upgrade,
                db_url,
                "head",
                bootstrap_legacy=True,
                lock_timeout_seconds=_LOCK_HOLD_TIMEOUT_SECONDS,
            )
            with pytest.raises(TimeoutError):
                # Blocked on the migration lock while the holder is migrating.
                future.result(timeout=1.0)
        result = future.result(timeout=_LOCK_HOLD_TIMEOUT_SECONDS)

    assert result.current_revision == _HEAD_REVISION
    assert result.bootstrap.stamped_revision is None
    assert any("skipping upgrade" in record.getMessage() for record in caplog.records)


def test_run_upgrade_times_out_with_actionable_error_when_lock_is_held(tmp_path: Path) -> None:
    db_path = tmp_path / "timeout.sqlite"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    run_upgrade(db_url, "head", bootstrap_legacy=True)

    sync_url = to_sync_database_url(db_url)
    with migration_lock(sync_url, timeout_seconds=_LOCK_HOLD_TIMEOUT_SECONDS):
        with pytest.raises(TimeoutError) as excinfo:
            run_upgrade(db_url, "head", bootstrap_legacy=True, lock_timeout_seconds=0.5)

    message = str(excinfo.value)
    assert "migration lock" in message
    assert "database_migration_lock_timeout_seconds" in message


@pytest.mark.skipif(
    not _is_postgresql_database_url(_DATABASE_URL),
    reason="PostgreSQL-only advisory migration lock test",
)
def test_postgresql_run_upgrade_times_out_when_advisory_lock_is_held() -> None:
    sync_url = to_sync_database_url(_DATABASE_URL)
    with migration_lock(sync_url, timeout_seconds=_LOCK_HOLD_TIMEOUT_SECONDS):
        with pytest.raises(TimeoutError) as excinfo:
            run_upgrade(_DATABASE_URL, "head", bootstrap_legacy=True, lock_timeout_seconds=0.5)

    message = str(excinfo.value)
    assert "migration lock" in message
    assert "database_migration_lock_timeout_seconds" in message


def test_migration_lock_is_noop_for_in_memory_sqlite() -> None:
    # In-memory databases are process-private; nested acquisition proves no-op.
    with migration_lock("sqlite:///:memory:", timeout_seconds=0.1):
        with migration_lock("sqlite:///:memory:", timeout_seconds=0.1):
            pass


def test_concurrent_cli_upgrades_share_one_sqlite_database(tmp_path: Path) -> None:
    db_path = tmp_path / "cli-race.sqlite"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    def _run_cli() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "app.db.migrate", "--db-url", db_url, "upgrade"],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=_REPO_ROOT,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_run_cli) for _ in range(2)]
        completed = [future.result(timeout=300) for future in futures]

    for process in completed:
        assert process.returncode == 0, f"stdout={process.stdout}\nstderr={process.stderr}"
        assert f"current_revision={_HEAD_REVISION}" in process.stdout
    assert _sqlite_alembic_revisions(db_path) == [_HEAD_REVISION]


@pytest.mark.asyncio
async def test_schema_ahead_of_build_reports_newer_not_behind(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "ahead.sqlite"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    run_upgrade(db_url, "head", bootstrap_legacy=True)

    engine = create_async_engine(db_url, future=True)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE alembic_version SET version_num = :revision"),
                {"revision": _FUTURE_REVISION},
            )
    finally:
        await engine.dispose()

    state = inspect_migration_state(db_url)
    assert state.is_ahead
    assert state.unknown_revisions == (_FUTURE_REVISION,)
    assert state.needs_upgrade

    with pytest.raises(MigrationBootstrapError) as upgrade_error:
        run_upgrade(db_url, "head", bootstrap_legacy=True)
    upgrade_message = str(upgrade_error.value)
    assert "not known to this build" in upgrade_message
    assert _FUTURE_REVISION in upgrade_message
    assert "Unsupported alembic_version" not in upgrade_message

    import app.db.session as session_module

    monkeypatch.setattr(session_module._settings, "database_url", db_url)
    monkeypatch.setattr(session_module._settings, "database_migrate_on_startup", False)
    with pytest.raises(RuntimeError) as startup_error:
        await session_module.init_db()
    startup_message = str(startup_error.value)
    assert "not known to this build" in startup_message
    assert _FUTURE_REVISION in startup_message
    assert "behind Alembic head" not in startup_message
