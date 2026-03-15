# Neon PostgreSQL Runtime

## Why
The repo still defaults to SQLite in runtime, tests, and docs, which conflicts with the intended deployment model of using Neon as the primary remote database.

## What Changes
- Make PostgreSQL on Neon the required runtime backend.
- Add a dedicated migration URL setting for startup migrations and Alembic.
- Remove SQLite-specific startup behavior from the runtime path.
- Update Docker, env templates, and tests to stop assuming SQLite defaults.

## Impact
- Runtime now fails fast if Neon database URLs are missing.
- Docker Compose no longer provisions local PostgreSQL.
- Existing SQLite files are not migrated; PostgreSQL starts fresh.
