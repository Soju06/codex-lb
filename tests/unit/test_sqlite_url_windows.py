from __future__ import annotations

import os
import sqlite3
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.migrate import _build_alembic_config
from app.db.migration_url import to_sync_database_url
from app.db.sqlite_utils import normalize_sqlite_url, sqlite_db_path_from_url
from app.modules.usage.repository import UsageRepository

# The shape SQLAlchemy's `URL.render_as_string()` produces for the Windows
# default SQLite path `C:\Users\me\.codex-lb\store.db` on a platform that
# percent-encodes backslashes (the exact string that reaches
# `sqlite_db_path_from_url` and `_build_alembic_config` at runtime on Windows,
# and the shape CI reproduces on Linux). Using the encoded form directly keeps
# the regression test platform-independent.
ENCODED_WINDOWS_URL = "sqlite:///C%3A%5CUsers%5Cme%5C.codex-lb%5Cstore.db"
DECODED_WINDOWS_PATH = r"C:\Users\me\.codex-lb\store.db"


def _encoded_windows_sqlite_url_for_path(path: Path) -> str:
    return "sqlite:///" + urllib.parse.quote(str(path), safe="")


def _dashboard_fixture_path_and_url(tmp_path: Path) -> tuple[Path, str]:
    if os.name == "nt":
        decoded_db_path = tmp_path / "store.db"
        return decoded_db_path, _encoded_windows_sqlite_url_for_path(decoded_db_path)
    return Path(DECODED_WINDOWS_PATH), ENCODED_WINDOWS_URL


class TestSqlitePathFromUrlWindows:
    """Regression: ``sqlite_db_path_from_url`` must percent-decode the path.

    Before the fix, the literal ``C%3A%5CUsers%5C...`` reached
    ``sqlite3.connect()``, which either failed with "unable to open database
    file" or created a stray 0-byte database next to the CWD, breaking
    ``/api/accounts`` and ``/api/dashboard/overview`` with
    ``no such table: accounts`` while ``/health`` stayed 200.
    """

    def test_decodes_percent_encoded_windows_path(self) -> None:
        path = sqlite_db_path_from_url(ENCODED_WINDOWS_URL)
        assert path is not None
        # the real filesystem path is returned, not the percent-escaped literal
        assert str(path) == DECODED_WINDOWS_PATH

    def test_memory_database_returns_none(self) -> None:
        assert sqlite_db_path_from_url("sqlite+aiosqlite:///:memory:") is None

    def test_normalize_decodes_percent_encoded_file_path(self) -> None:
        assert normalize_sqlite_url(ENCODED_WINDOWS_URL) == f"sqlite:///{DECODED_WINDOWS_PATH}"

    def test_decodes_encoded_drive_with_unescaped_separators(self) -> None:
        url = "sqlite:///C%3A/Users/me/.codex-lb/store.db"

        path = sqlite_db_path_from_url(url)

        assert path is not None
        assert str(path) == "C:/Users/me/.codex-lb/store.db"
        assert normalize_sqlite_url(url) == "sqlite:///C:/Users/me/.codex-lb/store.db"

    def test_decodes_lowercase_encoded_drive_with_unescaped_separators(self) -> None:
        url = "sqlite:///c%3A/Users/me/.codex-lb/store.db"

        path = sqlite_db_path_from_url(url)

        assert path is not None
        assert str(path) == "c:/Users/me/.codex-lb/store.db"
        assert normalize_sqlite_url(url) == "sqlite:///c:/Users/me/.codex-lb/store.db"

    def test_preserves_literal_percent_sequences_from_data_dir_defaults(self) -> None:
        url = "sqlite+aiosqlite:////var/lib/codex%20lb/store.db"

        path = sqlite_db_path_from_url(url)

        assert path is not None
        assert str(path) == "/var/lib/codex%20lb/store.db"
        assert normalize_sqlite_url(url) == url

    def test_normalize_preserves_query_and_fragment(self) -> None:
        assert (
            normalize_sqlite_url(f"{ENCODED_WINDOWS_URL}?mode=ro#db") == f"sqlite:///{DECODED_WINDOWS_PATH}?mode=ro#db"
        )

    def test_decodes_encoded_hash_at_filesystem_boundary(self) -> None:
        url = "sqlite:///C%3A%5CUsers%5Cme%5Cfoo%23bar%5Cstore.db"

        path = sqlite_db_path_from_url(url)

        assert path is not None
        assert str(path) == r"C:\Users\me\foo#bar\store.db"

    def test_normalize_keeps_encoded_hash_in_url_path(self) -> None:
        url = "sqlite:///C%3A%5CUsers%5Cme%5Cfoo%23bar%5Cstore.db"

        normalized = normalize_sqlite_url(url)

        assert normalized == r"sqlite:///C:\Users\me\foo#bar\store.db"
        assert sqlite_db_path_from_url(normalized) == Path(r"C:\Users\me\foo#bar\store.db")

    def test_normalize_preserves_decoded_windows_space_and_percent_characters(self) -> None:
        url = "sqlite:///C%3A%5CUsers%5CFirst%20Last%5C100%25%5Cstore.db"

        normalized = normalize_sqlite_url(url)

        assert normalized == r"sqlite:///C:\Users\First Last\100%\store.db"
        assert sqlite_db_path_from_url(normalized) == Path(r"C:\Users\First Last\100%\store.db")

    def test_raw_windows_literal_percent_path_is_not_decoded(self) -> None:
        url = r"sqlite:///C:\codex%20lb\store.db"

        path = sqlite_db_path_from_url(url)

        assert path is not None
        assert str(path) == r"C:\codex%20lb\store.db"
        assert normalize_sqlite_url(url) == url

    def test_raw_windows_literal_encoded_hash_path_is_not_decoded(self) -> None:
        url = r"sqlite:///C:\data%23set\store.db"

        path = sqlite_db_path_from_url(url)

        assert path is not None
        assert str(path) == r"C:\data%23set\store.db"
        assert normalize_sqlite_url(url) == url

    def test_lowercase_windows_encoded_drive_segment_is_decoded(self) -> None:
        url = "sqlite+aiosqlite:///c%3A/cache.db"

        path = sqlite_db_path_from_url(url)

        assert path is not None
        assert str(path) == "c:/cache.db"
        assert normalize_sqlite_url(url) == "sqlite+aiosqlite:///c:/cache.db"


class TestBuildAlembicConfigWindowsUrl:
    """Regression: ``_build_alembic_config`` must escape ``%`` before ConfigParser.

    Before the fix, ``set_main_option`` raised ``ValueError: invalid
    interpolation syntax`` because Alembic's ``configparser`` uses
    ``BasicInterpolation``, which treats a bare ``%`` as interpolation syntax.
    On CI (Linux) the encoded URL above keeps its ``%`` so this test fails
    without the ``%%`` escape; on Windows the encoded form round-trips without
    ``%`` so the assertion is trivially satisfied there.
    """

    def test_does_not_raise_on_percent_encoded_url(self) -> None:
        # Must not raise ValueError: invalid interpolation syntax
        config = _build_alembic_config(ENCODED_WINDOWS_URL)
        assert isinstance(config, Config)

    def test_url_round_trips_through_configparser(self) -> None:
        config = _build_alembic_config(ENCODED_WINDOWS_URL)
        # get_main_option decodes '%%' back to '%', so SQLAlchemy sees the
        # original encoded URL unchanged.
        sqlalchemy_url = config.get_main_option("sqlalchemy.url")
        assert sqlalchemy_url is not None
        assert sqlalchemy_url == to_sync_database_url(ENCODED_WINDOWS_URL)
        assert r"C:\Users\me\.codex-lb\store.db" in sqlalchemy_url


def test_to_sync_database_url_decodes_percent_encoded_sqlite_path() -> None:
    assert to_sync_database_url(ENCODED_WINDOWS_URL) == f"sqlite:///{DECODED_WINDOWS_PATH}"


def test_to_sync_database_url_preserves_literal_percent_path() -> None:
    literal_percent_url = "sqlite+aiosqlite:////var/lib/codex%20lb/store.db"

    assert to_sync_database_url(literal_percent_url) == "sqlite:////var/lib/codex%20lb/store.db"


def test_background_engine_creation_decodes_percent_encoded_sqlite_url(tmp_path, monkeypatch) -> None:
    from app.db import session as session_module

    decoded_db_path = DECODED_WINDOWS_PATH
    encoded_url = ENCODED_WINDOWS_URL.replace("sqlite:///", "sqlite+aiosqlite:///")
    created_urls: list[str] = []

    class FakeEngine:
        sync_engine = object()

    def fake_create_async_engine(url: str, **kwargs):
        del kwargs
        created_urls.append(url)
        return FakeEngine()

    monkeypatch.setattr(session_module, "create_async_engine", fake_create_async_engine)
    monkeypatch.setattr(session_module, "_configure_sqlite_engine", lambda *args, **kwargs: None)
    monkeypatch.setattr(session_module, "async_sessionmaker", lambda *args, **kwargs: object())

    try:
        session_module.init_background_db(encoded_url)

        assert created_urls == [f"sqlite+aiosqlite:///{decoded_db_path}"]
    finally:
        session_module._background_engine = None
        session_module._background_session_factory = None


def test_background_engine_creation_decodes_encoded_hash_for_sqlalchemy(monkeypatch) -> None:
    from app.db import session as session_module

    encoded_url = "sqlite+aiosqlite:///C%3A%5Cdata%23set%5Cstore.db"
    created_urls: list[str] = []

    class FakeEngine:
        sync_engine = object()

    def fake_create_async_engine(url: str, **kwargs):
        del kwargs
        created_urls.append(url)
        return FakeEngine()

    monkeypatch.setattr(session_module, "create_async_engine", fake_create_async_engine)
    monkeypatch.setattr(session_module, "_configure_sqlite_engine", lambda *args, **kwargs: None)
    monkeypatch.setattr(session_module, "async_sessionmaker", lambda *args, **kwargs: object())

    try:
        session_module.init_background_db(encoded_url)

        assert created_urls == [r"sqlite+aiosqlite:///C:\data#set\store.db"]
    finally:
        session_module._background_engine = None
        session_module._background_session_factory = None


@pytest.mark.asyncio
async def test_startup_init_db_decodes_percent_encoded_sqlite_path(tmp_path, monkeypatch) -> None:
    """Regression: startup must not create/check the percent-literal DB path.

    The production failure happens before serving requests when startup walks
    through ``init_db()``. This exercises that product path instead of only the
    helper that decodes URLs.
    """

    from app.db import session as session_module
    from app.db.sqlite_utils import IntegrityCheck

    monkeypatch.chdir(tmp_path)
    decoded_db_path, encoded_url = _dashboard_fixture_path_and_url(tmp_path)
    encoded_url = encoded_url.replace("sqlite:///", "sqlite+aiosqlite:///")
    checked_paths: list[str] = []

    monkeypatch.setattr(
        session_module,
        "_settings",
        SimpleNamespace(
            database_url=encoded_url,
            database_sqlite_startup_check_mode="quick",
            database_migrate_on_startup=True,
            database_sqlite_pre_migrate_backup_enabled=False,
            database_migrations_fail_fast=True,
        ),
    )

    def fake_check_sqlite_integrity(path, *, mode):
        del mode
        checked_paths.append(str(path))
        return IntegrityCheck(ok=True, details=None)

    async def fake_run_startup_migrations(database_url: str):
        assert database_url == f"sqlite+aiosqlite:///{decoded_db_path}"
        return SimpleNamespace(
            current_revision="head",
            bootstrap=SimpleNamespace(stamped_revision=None, legacy_row_count=0),
        )

    monkeypatch.setattr(session_module, "check_sqlite_integrity", fake_check_sqlite_integrity)
    monkeypatch.setattr(
        session_module,
        "_load_migration_entrypoints",
        lambda: (
            lambda database_url: SimpleNamespace(needs_upgrade=False),
            fake_run_startup_migrations,
            lambda database_url: (),
        ),
    )

    await session_module.init_db()

    assert checked_paths == [str(decoded_db_path)]
    assert not (tmp_path / "C%3A%5CUsers%5Cme%5C.codex-lb%5Cstore.db").exists()


@pytest.mark.asyncio
async def test_startup_init_db_preserves_literal_percent_data_dir_path(tmp_path, monkeypatch) -> None:
    """Regression: a raw data_dir path containing ``%20`` is not URL-decoded."""

    from app.db import session as session_module
    from app.db.sqlite_utils import IntegrityCheck

    literal_percent_db_path = tmp_path / "codex%20lb" / "store.db"
    literal_percent_url = f"sqlite+aiosqlite:///{literal_percent_db_path}"
    checked_paths: list[str] = []

    monkeypatch.setattr(
        session_module,
        "_settings",
        SimpleNamespace(
            database_url=literal_percent_url,
            database_sqlite_startup_check_mode="quick",
            database_migrate_on_startup=True,
            database_sqlite_pre_migrate_backup_enabled=False,
            database_migrations_fail_fast=True,
        ),
    )

    def fake_check_sqlite_integrity(path, *, mode):
        del mode
        checked_paths.append(str(path))
        return IntegrityCheck(ok=True, details=None)

    async def fake_run_startup_migrations(database_url: str):
        assert database_url == literal_percent_url
        return SimpleNamespace(
            current_revision="head",
            bootstrap=SimpleNamespace(stamped_revision=None, legacy_row_count=0),
        )

    monkeypatch.setattr(session_module, "check_sqlite_integrity", fake_check_sqlite_integrity)
    monkeypatch.setattr(
        session_module,
        "_load_migration_entrypoints",
        lambda: (
            lambda database_url: SimpleNamespace(needs_upgrade=False),
            fake_run_startup_migrations,
            lambda database_url: (),
        ),
    )

    await session_module.init_db()

    assert checked_paths == [str(literal_percent_db_path)]
    assert literal_percent_db_path.parent.is_dir()
    assert not (tmp_path / "codex lb").exists()


@pytest.mark.asyncio
async def test_dashboard_usage_sqlite_fast_path_decodes_percent_encoded_bind_url(tmp_path, monkeypatch) -> None:
    """Regression: dashboard/account usage reads open the real decoded DB file.

    ``/api/accounts`` and ``/api/dashboard/overview`` reach usage data through
    ``DashboardRepository.latest_usage_by_account`` /
    ``UsageRepository.latest_by_account``. On SQLite that path intentionally
    uses direct read-only ``sqlite3.connect`` calls, so it must decode an
    encoded SQLAlchemy bind URL before opening the file.
    """

    monkeypatch.chdir(tmp_path)
    decoded_db_path, encoded_url = _dashboard_fixture_path_and_url(tmp_path)

    with sqlite3.connect(decoded_db_path) as conn:
        conn.execute("create table accounts (id text primary key)")
        conn.execute(
            """
            create table usage_history (
                id integer primary key,
                account_id text not null,
                recorded_at text not null,
                window text,
                used_percent real not null,
                input_tokens integer,
                output_tokens integer,
                reset_at integer,
                window_minutes integer,
                credits_has integer,
                credits_unlimited integer,
                credits_balance real
            )
            """
        )
        conn.execute("insert into accounts (id) values (?)", ("acc_dash",))
        conn.execute(
            """
            insert into usage_history (
                id, account_id, recorded_at, window, used_percent, window_minutes
            )
            values (?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "acc_dash",
                datetime(2026, 7, 18, 0, 0, tzinfo=UTC).isoformat(),
                "primary",
                42.0,
                300,
            ),
        )

    bind = SimpleNamespace(
        dialect=SimpleNamespace(name="sqlite"),
        url=encoded_url,
    )
    repository = UsageRepository(cast(AsyncSession, SimpleNamespace(get_bind=lambda: bind)))

    latest = await repository.latest_by_account("primary")

    assert latest["acc_dash"].used_percent == pytest.approx(42.0)
    assert not Path("C%3A%5CUsers%5Cme%5C.codex-lb%5Cstore.db").exists()
