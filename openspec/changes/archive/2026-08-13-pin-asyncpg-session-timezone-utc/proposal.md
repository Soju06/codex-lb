# Pin Asyncpg Session Time Zone to UTC

## Why

`codex-lb` stores naive UTC `datetime` values in PostgreSQL `timestamptz`
columns. `asyncpg` binds a naive datetime using the database session time zone,
so a PostgreSQL session inheriting a non-UTC default silently shifts persisted
timestamps away from real UTC. That corrupts wall-clock comparisons used by
bridge-ring heartbeats, leader election, cleanup cutoffs, and account or stream
lease expiry.

## What Changes

- Configure every `postgresql+asyncpg://` SQLAlchemy async engine with
  `server_settings.timezone=UTC`.
- Preserve the existing PostgreSQL test tuning for asyncpg prepared-statement
  caching.
- Add unit coverage for the engine kwargs and a PostgreSQL integration
  regression that forces the database default time zone away from UTC before
  opening a new app-configured asyncpg engine.

## Impact

- Affected spec: `database-backends`
- Affected code: `app/db/session.py`
- Affected tests: `tests/unit/test_db_session.py`,
  `tests/integration/test_db_session_timezone.py`
