from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_upgrade_unknown_revision_reports_guarded_recovery_without_changes(tmp_path: Path) -> None:
    database = tmp_path / "future.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE alembic_version (version_num VARCHAR(255) PRIMARY KEY);"
            "INSERT INTO alembic_version VALUES ('20990101_000000_future');"
            "CREATE TABLE preserved_data (value TEXT);"
            "INSERT INTO preserved_data VALUES ('synthetic-sentinel');"
        )
    before = database.read_bytes()
    url = f"sqlite+aiosqlite:///{database}"
    result = subprocess.run(
        [sys.executable, "-m", "app.db.migrate", "upgrade", "head"],
        env={**os.environ, "CODEX_LB_DATABASE_URL": url, "CODEX_LB_TEST_DATABASE_URL": url},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode != 0
    assert "MigrationBootstrapError" in result.stderr
    assert "20990101_000000_future" in result.stderr
    assert "Deploy a matching or newer image" in result.stderr
    assert database.read_bytes() == before
    assert "python -m app.db.migrate stamp <revision>" in result.stderr
    assert "schema and data compatibility" in result.stderr
    assert "both the recorded and target revisions" in result.stderr
    assert "migration transactions have ended" in result.stderr
    assert "database backup and encryption key" in result.stderr
    assert "does not roll back schema or data" in result.stderr
    assert "synthetic-sentinel" not in result.stderr


def test_old_build_cannot_stamp_unknown_revision(tmp_path: Path) -> None:
    database = tmp_path / "future.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE alembic_version (version_num VARCHAR(255) PRIMARY KEY);"
            "INSERT INTO alembic_version VALUES ('20990101_000000_future');"
        )
    before = database.read_bytes()
    url = f"sqlite+aiosqlite:///{database}"
    result = subprocess.run(
        [sys.executable, "-m", "app.db.migrate", "stamp", "head"],
        env={**os.environ, "CODEX_LB_DATABASE_URL": url, "CODEX_LB_TEST_DATABASE_URL": url},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode != 0
    assert "Can't locate revision identified by '20990101_000000_future'" in result.stderr
    assert database.read_bytes() == before
