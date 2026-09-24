from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.db.migration_url import to_sync_database_url

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
BASE = "20260909_120000_dashboard_conversation_archive"
TARGET = "20260910_000000_request_logs_missing_cost_index"
HEAD = ScriptDirectory(str(ROOT / "app/db/alembic")).get_current_head()


@pytest.fixture
def disposable_url() -> Iterator[str]:
    url = os.environ.get("CODEX_LB_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("requires disposable PostgreSQL with CREATEDB; failure injection also requires a superuser")
    database = "migration_benchmark_" + uuid4().hex
    engine = create_engine(to_sync_database_url(url), isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database}"'))
    try:
        yield make_url(url).set(database=database).render_as_string(hide_password=False)
    finally:
        with engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
        engine.dispose()


def invoke(url: str, output: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "CODEX_LB_DATABASE_URL": url, "CODEX_LB_TEST_DATABASE_URL": url, "BENCHMARK_DATABASE_URL": url}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.benchmark_migrations",
            "--db-url-env",
            "BENCHMARK_DATABASE_URL",
            "--disposable",
            "--base",
            BASE,
            "--target",
            TARGET,
            "--rows",
            "12",
            "--accounts",
            "3",
            "--seed",
            "7",
            "--output",
            str(output),
            *extra,
        ],
        env=env,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=180,
    )


def test_benchmark_measures_selected_revisions(disposable_url: str, tmp_path: Path) -> None:
    output = tmp_path / "measurement"
    result = invoke(disposable_url, output)
    assert result.returncode == 0, result.stderr
    assert disposable_url not in result.args
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "completed"
    assert [step["revision"] for step in report["steps"]] == [
        "20260909_130000_add_request_logs_live_facet_indexes",
        TARGET,
    ]
    assert all(step["elapsed_seconds"] > 0 and step["returncode"] == 0 for step in report["steps"])
    assert [step["resulting_revisions"] for step in report["steps"]] == [
        ["20260909_130000_add_request_logs_live_facet_indexes"],
        [TARGET],
    ]
    assert report["resulting_revisions"] == [TARGET]
    assert report["fixture"]["observed"]["rows"] == 12
    assert report["fixture"]["observed"]["accounts"] == 3
    assert report["check"]["target_is_head"] == (TARGET == HEAD)
    if TARGET == HEAD:
        assert report["check"]["returncode"] == 0
    assert "separate" in (output / "report.md").read_text()
    assert "benchmark-disposable" not in (output / "report.json").read_text()


@pytest.mark.parametrize("target,rows", [(HEAD, "12"), (BASE, "0")])
def test_benchmark_noop_preserves_selected_target(
    disposable_url: str,
    tmp_path: Path,
    target: str,
    rows: str,
) -> None:
    output = tmp_path / "noop"
    result = invoke(disposable_url, output, "--base", target, "--target", target, "--rows", rows)
    assert result.returncode == 0
    report = json.loads((output / "report.json").read_text())
    assert report["noop"] is True
    assert report["steps"] == []
    assert report["resulting_revisions"] == [target]
    assert report["check"]["target_is_head"] == (target == HEAD)
    assert isinstance(report["check"]["returncode"], int)
    if target == HEAD:
        assert report["check"]["returncode"] == 0
    assert report["fixture"]["observed"]["rows"] == int(rows)


def test_benchmark_rejects_populated_target(disposable_url: str, tmp_path: Path) -> None:
    engine = create_engine(to_sync_database_url(disposable_url))
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE preserved (value integer)"))
        connection.execute(text("INSERT INTO preserved VALUES (42)"))
    output = tmp_path / "occupied"
    assert invoke(disposable_url, output).returncode == 1
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "failed"
    assert report["stage"] == "empty_database_check"
    assert report["steps"] == []
    with engine.connect() as connection:
        assert connection.execute(text("SELECT value FROM preserved")).scalar_one() == 42
        assert connection.execute(text("SELECT to_regclass('public.alembic_version')")).scalar_one() is None
    engine.dispose()


@pytest.mark.parametrize("extra", [("--base", "head"), ("--base", TARGET, "--target", BASE)])
def test_benchmark_rejects_invalid_range(disposable_url: str, tmp_path: Path, extra: tuple[str, ...]) -> None:
    output = tmp_path / "invalid"
    assert invoke(disposable_url, output, *extra).returncode == 1
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "failed"
    assert report["stage"] == "validation"
    assert report["steps"] == []


@pytest.mark.parametrize("repeat", [1, 2])
def test_benchmark_fixture_is_repeatable(disposable_url: str, tmp_path: Path, repeat: int) -> None:
    output = tmp_path / str(repeat)
    result = invoke(disposable_url, output)
    assert result.returncode == 0
    fixture = json.loads((output / "report.json").read_text())["fixture"]
    assert fixture["version"] == "modulo-v1"
    assert fixture["observed"]["by_status"] == {"error": 1, "success": 11}
    assert fixture["observed"]["by_model"] == {"gpt-5": 4, "gpt-5-mini": 4, "gpt-5-codex": 4}
    assert fixture["observed"]["by_account"] == {
        "benchmark-account-1": 4,
        "benchmark-account-2": 4,
        "benchmark-account-3": 4,
    }
    assert fixture["observed"]["missing_cost_rows"] == 6
    assert fixture["observed"]["useragent_with_slash_rows"] == 6


def test_benchmark_reports_failed_revision(disposable_url: str, tmp_path: Path) -> None:
    engine = create_engine(to_sync_database_url(disposable_url))
    with engine.begin() as connection:
        assert connection.execute(text("SELECT current_setting('is_superuser')")).scalar_one() == "on", (
            "failure-injection test requires a PostgreSQL superuser on the disposable server; "
            "CREATEDB alone is insufficient"
        )
        connection.execute(
            text("""
            CREATE FUNCTION reject_benchmark_index() RETURNS event_trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF current_query() LIKE '%CREATE INDEX%idx_logs_missing_cost%' THEN
                    RAISE EXCEPTION 'synthetic failure benchmark-disposable';
                END IF;
            END $$
        """)
        )
        connection.execute(
            text("""
            CREATE EVENT TRIGGER benchmark_failure ON ddl_command_start
            WHEN TAG IN ('CREATE INDEX') EXECUTE FUNCTION reject_benchmark_index()
        """)
        )
    engine.dispose()
    output = tmp_path / "failed"
    result = invoke(disposable_url, output)
    assert result.returncode == 1
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "failed"
    assert report["stage"] == "upgrade"
    assert report["error"] == "revision upgrade failed"
    assert report["steps"][-1]["revision"] == TARGET
    assert report["steps"][-1]["returncode"] != 0
    assert report["steps"][-1]["elapsed_seconds"] > 0
    assert [step["resulting_revisions"] for step in report["steps"]] == [
        ["20260909_130000_add_request_logs_live_facet_indexes"],
        ["20260909_130000_add_request_logs_live_facet_indexes"],
    ]
    assert report["resulting_revisions"] == ["20260909_130000_add_request_logs_live_facet_indexes"]
    assert "check" not in report
    assert "benchmark-disposable" not in (output / "report.json").read_text()


def test_benchmark_accepts_maximum_seed(disposable_url: str, tmp_path: Path) -> None:
    output = tmp_path / "maximum-seed"
    result = invoke(disposable_url, output, "--seed", "2147483647", "--rows", "1", "--target", BASE)
    assert result.returncode == 0
    fixture = json.loads((output / "report.json").read_text())["fixture"]
    assert fixture["seed"] == 2147483647
    assert fixture["observed"]["rows"] == 1
    assert fixture["observed"]["by_account"] == {"benchmark-account-3": 1}


@pytest.mark.parametrize("value", [None, ""])
def test_benchmark_requires_selected_url_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv("MISSING_BENCHMARK_URL", raising=False)
    else:
        monkeypatch.setenv("MISSING_BENCHMARK_URL", value)
    output = tmp_path / "missing-url"
    result = invoke(
        "postgresql+asyncpg://synthetic:synthetic@127.0.0.1:1/disposable",
        output,
        "--db-url-env",
        "MISSING_BENCHMARK_URL",
    )
    assert result.returncode == 2
    assert "selected URL environment variable must be nonempty" in result.stderr
    assert not output.exists()
