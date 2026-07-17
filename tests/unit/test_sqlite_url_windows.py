from __future__ import annotations

from alembic.config import Config

from app.db.migrate import _build_alembic_config
from app.db.migration_url import to_sync_database_url
from app.db.sqlite_utils import sqlite_db_path_from_url

# The shape SQLAlchemy's `URL.render_as_string()` produces for the Windows
# default SQLite path `C:\Users\me\.codex-lb\store.db` on a platform that
# percent-encodes backslashes (the exact string that reaches
# `sqlite_db_path_from_url` and `_build_alembic_config` at runtime on Windows,
# and the shape CI reproduces on Linux). Using the encoded form directly keeps
# the regression test platform-independent.
ENCODED_WINDOWS_URL = "sqlite:///C%3A%5CUsers%5Cme%5C.codex-lb%5Cstore.db"


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
        assert str(path) == r"C:\Users\me\.codex-lb\store.db"

    def test_memory_database_returns_none(self) -> None:
        assert sqlite_db_path_from_url("sqlite+aiosqlite:///:memory:") is None


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
        assert config.get_main_option("sqlalchemy.url") == to_sync_database_url(
            ENCODED_WINDOWS_URL
        )
