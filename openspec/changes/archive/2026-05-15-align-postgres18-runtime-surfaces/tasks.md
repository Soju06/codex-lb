## 1. PostgreSQL 18 Surface Alignment

- [x] 1.1 Update local Compose PostgreSQL to `postgres:18-alpine`.
- [x] 1.2 Mount the Compose PostgreSQL data volume at `/var/lib/postgresql` for
  PostgreSQL 18 Docker compatibility.
- [x] 1.3 Preload `pg_stat_statements` in the Compose PostgreSQL profile.
- [x] 1.4 Update PostgreSQL-backed CI service images to `postgres:18`.
- [x] 1.5 Update Helm PostgreSQL helper images to PostgreSQL 18.

## 2. Operator Context

- [x] 2.1 Document that local Compose and CI PostgreSQL coverage track
  PostgreSQL 18.
- [x] 2.2 Document the PostgreSQL 18 Docker mount path and
  `pg_stat_statements` restart/extension requirements.

## 3. Verification

- [x] 3.1 Prove a live PostgreSQL backup restores into a throwaway PostgreSQL 18
  container before removing rollback containers.
- [x] 3.2 Verify the live PostgreSQL container preloads `pg_stat_statements` and
  exposes the extension.
- [x] 3.3 Rebuild/restart `codex-lb-direct` and verify readiness, Docker health,
  schema drift, metrics, worker shape, and PostgreSQL version.
- [x] 3.4 Validate `git diff --check`, `docker compose config --quiet`, and
  `openspec validate --specs`.
