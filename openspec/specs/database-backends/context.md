## Overview

codex-lb is designed to be SQLite-first for simple local usage and container defaults. SQLite-specific resilience behavior (integrity checks, WAL tuning, recovery tooling) remains valuable for the default mode.

For higher concurrency or infrastructure-managed deployments, PostgreSQL support is enabled through SQLAlchemy async URLs using `asyncpg`.

## Decisions

- Keep SQLite as default to preserve zero-config startup.
- Accept PostgreSQL through `CODEX_LB_DATABASE_URL` only; no new configuration key aliases.
- Keep SQLite-specific recovery tooling SQLite-only; PostgreSQL operations should use PostgreSQL-native backup/recovery practices.

## Operational Notes

- SQLite default URL: `sqlite+aiosqlite:///~/.codex-lb/store.db`
- PostgreSQL example URL: `postgresql+asyncpg://codex_lb:codex_lb@127.0.0.1:5432/codex_lb`
- Pool controls (`database_pool_size`, `database_max_overflow`, `database_pool_timeout_seconds`) apply to non-memory SQLite and PostgreSQL engine creation.

## Example

Use PostgreSQL while keeping all other defaults:

```bash
CODEX_LB_DATABASE_URL=postgresql+asyncpg://codex_lb:codex_lb@127.0.0.1:5432/codex_lb codex-lb
```
