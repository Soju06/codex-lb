## 1. Implementation

- [x] 1.1 `sqlite_db_path_from_url` percent-decodes the path before `Path(path)` so Windows-style encoded URLs resolve to the real file (`app/db/sqlite_utils.py`)
- [x] 1.2 `_build_alembic_config` escapes `%` -> `%%` before `set_main_option` so Alembic `BasicInterpolation` does not raise on a percent-encoded Windows path (`app/db/migrate.py`)

## 2. Validation

- [x] 2.1 Regression tests at the failing surface: a Windows-style `sqlite+aiosqlite:///C:\Users\...` URL exercises both `sqlite_db_path_from_url` (decoded path returned) and `_build_alembic_config` (no `ValueError`; URL round-trips through `get_main_option`); both fail without the fix (`tests/unit/test_sqlite_url_windows.py`)
- [x] 2.2 POSIX-style and `:memory:` SQLite URLs are unchanged (no behavior change off Windows)
- [x] 2.3 `ruff` / `ty`; `openspec validate --specs`
