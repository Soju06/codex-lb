# Design

## Scope

This is a narrow deployment-surface alignment. It does not change application
database APIs, migration semantics, request handling, or Helm install modes.

## Decisions

- Use PostgreSQL 18 everywhere this repo provisions PostgreSQL for local or CI
  verification.
- Keep the live direct runtime on the already-proved PostgreSQL 18 data volume
  and require backup/restore proof before removing stopped rollback containers.
- Mount the local Compose data volume at `/var/lib/postgresql` so the image can
  manage the version-specific internal `PGDATA` path.
- Enable `pg_stat_statements` through preload settings because the extension
  requires restart-time shared library loading.

## Verification

Verification is config and runtime focused: render Compose, validate OpenSpec,
restart the direct container, prove readiness/schema drift, and confirm
`pg_stat_statements` is loaded in PostgreSQL.

