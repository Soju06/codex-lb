## Overview

codex-lb runs against PostgreSQL on Neon for all supported runtime deployments. The runtime contract is split between a pooled application DSN for normal ORM traffic and an optional direct DSN for Alembic and startup migration operations.

## Decisions

- Neon PostgreSQL is the required runtime backend.
- `CODEX_LB_DATABASE_URL` is the canonical pooled runtime DSN.
- `CODEX_LB_DATABASE_MIGRATION_URL` is the canonical migration DSN and falls back to `CODEX_LB_DATABASE_URL` when omitted.
- SQLite-specific recovery and validation tooling is no longer part of the runtime startup path.

## Operational Notes

- Runtime DSN example: `postgresql+asyncpg://USER:PASSWORD@ep-pooler.region.aws.neon.tech/codex_lb?sslmode=require`
- Migration DSN example: `postgresql+asyncpg://USER:PASSWORD@ep-direct.region.aws.neon.tech/codex_lb?sslmode=require`
- Pool controls (`database_pool_size`, `database_max_overflow`, `database_pool_timeout_seconds`) apply to the runtime async engine.
- Tests and CI should use a dedicated Neon database or branch via `CODEX_LB_TEST_DATABASE_URL` and `CODEX_LB_TEST_DATABASE_MIGRATION_URL`.

## Example

Use Neon with a pooled runtime connection and a direct migration connection:

```bash
CODEX_LB_DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@ep-pooler.region.aws.neon.tech/codex_lb?sslmode=require \
CODEX_LB_DATABASE_MIGRATION_URL=postgresql+asyncpg://USER:PASSWORD@ep-direct.region.aws.neon.tech/codex_lb?sslmode=require \
codex-lb
```
