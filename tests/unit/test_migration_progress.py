from __future__ import annotations

import logging
from contextlib import closing
from pathlib import Path

import pytest

from app.db.migrate import run_upgrade


def test_upgrade_reports_revision_execution_and_noop(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'progress.db'}"
    revision = "20260213_000000_base_schema"
    with caplog.at_level(logging.INFO, logger="app.db.migration_progress"):
        run_upgrade(url, revision, bootstrap_legacy=False)
    records = [record for record in caplog.records if record.name == "app.db.migration_progress"]
    assert [record.getMessage().split()[1] for record in records] == ["started", "executed"]
    assert all(f"revision={revision}" in record.getMessage() for record in records)
    assert "elapsed_seconds=" in records[-1].getMessage()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.db.migration_progress"):
        run_upgrade(url, revision, bootstrap_legacy=False)
    assert not [record for record in caplog.records if record.name == "app.db.migration_progress"]


def test_failed_upgrade_reports_active_revision_without_private_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import app.db.migrate as migrate

    scripts = tmp_path / "scripts"
    versions = scripts / "versions"
    versions.mkdir(parents=True)
    progress_log = tmp_path / "progress.log"
    scripts.joinpath("env.py").write_text(Path("app/db/alembic/env.py").read_text())
    versions.joinpath("first.py").write_text(
        "from pathlib import Path\nrevision = 'first'\ndown_revision = None\n"
        "def upgrade():\n"
        f"    assert 'Migration started revision=first' in Path({str(progress_log)!r}).read_text()\n"
    )
    versions.joinpath("broken.py").write_text(
        "revision = 'broken'\ndown_revision = 'first'\n"
        "def upgrade():\n    raise RuntimeError('private-sql-parameter')\n"
    )
    versions.joinpath("last.py").write_text("revision = 'last'\ndown_revision = 'broken'\ndef upgrade():\n    pass\n")
    monkeypatch.setattr(migrate, "_script_location", lambda: str(scripts))
    progress_logger = logging.getLogger("app.db.migration_progress")
    with closing(logging.FileHandler(progress_log)) as handler:
        progress_logger.addHandler(handler)
        try:
            with caplog.at_level(logging.INFO, logger="app.db.migration_progress"):
                with pytest.raises(RuntimeError, match="private-sql-parameter"):
                    run_upgrade(f"sqlite+aiosqlite:///{tmp_path / 'failure.db'}", "head", bootstrap_legacy=False)
        finally:
            progress_logger.removeHandler(handler)
    messages = [record.getMessage() for record in caplog.records if record.name == "app.db.migration_progress"]
    assert [message.split()[1:3] for message in messages] == [
        ["started", "revision=first"],
        ["executed", "revision=first"],
        ["started", "revision=broken"],
        ["failed", "revision=broken"],
    ]
    assert "private-sql-parameter" not in "\n".join(messages)
    assert float(messages[-1].split("elapsed_seconds=")[1]) >= 0


def test_cli_upgrade_prints_progress_to_stderr(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    url = f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}"
    result = subprocess.run(
        [sys.executable, "-m", "app.db.migrate", "upgrade", "20260213_000000_base_schema"],
        env={**os.environ, "CODEX_LB_DATABASE_URL": url, "CODEX_LB_TEST_DATABASE_URL": url},
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Migration started revision=20260213_000000_base_schema" in result.stderr
    assert "Migration executed revision=20260213_000000_base_schema" in result.stderr
    assert result.stdout.strip() == "current_revision=20260213_000000_base_schema"
