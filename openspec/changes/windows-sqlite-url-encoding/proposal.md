## Why

On Windows the default SQLite database URL is built from `Path.home() / ".codex-lb" / "store.db"`, an absolute path with a drive letter and backslashes (e.g. `sqlite+aiosqlite:///C:\Users\...\store.db`). SQLAlchemy's `URL.render_as_string()` percent-encodes that path into `sqlite:///C%3A%5CUsers%5C...%5Cstore.db`, and two code paths break on the resulting bare `%` characters:

- `app/db/migrate.py::_build_alembic_config` hands the encoded URL to Alembic's `ConfigParser` (`BasicInterpolation`), which treats `%` as interpolation syntax and raises `ValueError: invalid interpolation syntax` during startup (`inspect_migration_state`). The server never reaches "Application startup complete".
- `app/db/sqlite_utils.py::sqlite_db_path_from_url` returns the literal percent-escaped string, so `sqlite3.connect()` either fails with "unable to open database file" or silently creates a stray 0-byte database next to the CWD, breaking `/api/accounts` and `/api/dashboard/overview` with `no such table: accounts` while `/health` and `/v1/models` still return 200.

Non-Windows SQLite installs and PostgreSQL installs are unaffected (their paths contain no `%`).

## What Changes

- `sqlite_db_path_from_url` now `urllib.parse.unquote()`s the path before turning it into a `Path`, so `sqlite3.connect()` receives the real filesystem path on every platform. No-op on already-decoded paths.
- `_build_alembic_config` now escapes `%` -> `%%` before storing the URL in the Alembic `Config`. `get_main_option()` decodes `%%` back to `%`, so the URL SQLAlchemy receives is unchanged; the escape is transparent to every consumer of the config.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `database-backends`: SQLite file paths extracted from a SQLAlchemy URL MUST be percent-decoded before being handed to `sqlite3.connect()` so a Windows-style default database path resolves to the real file.
- `database-migrations`: The Alembic `Config` built for inspection/upgrade MUST escape `%` in the SQLAlchemy URL so `configparser` `BasicInterpolation` does not treat a percent-encoded Windows path as interpolation syntax.

## Impact

`app/db/sqlite_utils.py`, `app/db/migrate.py`. New regression tests in `tests/unit/test_sqlite_url_windows.py`. No schema, API, or settings change.
