# align-postgres18-runtime-surfaces

## Why

The live sustained-runtime baseline now runs on PostgreSQL 18 with query-level
statistics enabled. Local Compose, CI PostgreSQL jobs, and Helm migration helper
images still referenced PostgreSQL 16, which would let development and release
verification drift from the runtime shape that operators are actually running.

PostgreSQL 18 Docker images also place the default internal `PGDATA` below
`/var/lib/postgresql`, so examples that mount the named volume at the older
`/var/lib/postgresql/data` path are no longer the safest durable baseline.

## What Changes

- Align local Compose PostgreSQL on `postgres:18-alpine`.
- Mount the Compose PostgreSQL named volume at `/var/lib/postgresql`.
- Preload `pg_stat_statements` in the local Compose PostgreSQL profile.
- Align PostgreSQL-backed CI jobs on `postgres:18`.
- Align Helm PostgreSQL helper images on PostgreSQL 18.
- Record the PostgreSQL 18 mount and `pg_stat_statements` operational notes in
  database backend context.

## Impact

- Local and CI PostgreSQL coverage now matches the PostgreSQL 18 runtime
  baseline.
- Existing local PostgreSQL 16 data volumes may need an explicit backup/restore
  migration before being reused with PostgreSQL 18.
- No public API route, request, or response contract changes.
