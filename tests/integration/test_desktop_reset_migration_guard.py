"""Exercise the migration fixture through pytest without database connections."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("database", "explicit_database", "accepted"),
    [
        ("codex_lb", "codex_lb", False),
        ("production_test", "production_test", False),
        ("codex_lb_test", None, False),
        ("codex_lb_test", "other", False),
        ("codex_lb_test", "codex_lb_test", True),
    ],
)
def test_migration_fixture_checks_disposable_target_before_database_access(
    tmp_path: Path, database: str, explicit_database: str | None, accepted: bool
) -> None:
    probe = tmp_path / "test_probe.py"
    probe.write_text(
        """
from types import SimpleNamespace
import pytest
import tests.integration.test_desktop_reset_migration as migrations
from tests.integration.test_desktop_reset_migration import migration_url

@pytest.fixture
def db_setup():
    raise AssertionError("UNGUARDED_GLOBAL_DATABASE_RESET")

@pytest.fixture(autouse=True)
def isolate_database(monkeypatch):
    url = "postgresql+asyncpg://probe:probe@invalid.example/" + DATABASE
    monkeypatch.setattr(migrations, "get_settings", lambda: SimpleNamespace(database_url=url))
    if EXPLICIT_DATABASE is None:
        monkeypatch.delenv("CODEX_LB_TEST_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("CODEX_LB_TEST_DATABASE_URL", url.rsplit("/", 1)[0] + "/" + EXPLICIT_DATABASE)
    class Connection:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def execute(self, statement): pass
    class Engine:
        def begin(self): return Connection()
        async def dispose(self): pass
    def create_engine(target):
        assert ACCEPTED, "UNGUARDED_DATABASE_ACCESS"
        assert target == url
        return Engine()
    monkeypatch.setattr(migrations, "create_async_engine", create_engine)

@pytest.mark.parametrize("migration_url", ["postgresql"], indirect=True)
async def test_probe(migration_url):
    assert migration_url.endswith("/codex_lb_test")
""".replace("DATABASE\n", repr(database) + "\n")
        .replace("EXPLICIT_DATABASE", repr(explicit_database))
        .replace("ACCEPTED", repr(accepted))
    )
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--confcutdir", str(tmp_path), "-o", "asyncio_mode=auto", str(probe)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr
    assert "UNGUARDED" not in output, output
    if accepted:
        assert result.returncode == 0, output
    else:
        assert result.returncode == 1, output
        assert "Refusing to reset PostgreSQL" in output, output
