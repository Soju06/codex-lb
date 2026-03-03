## Context

codex-lb is SQLite-first and ships with SQLite-specific safety/recovery ergonomics. Core ORM usage is SQLAlchemy-based and mostly backend-agnostic, and sticky-session upsert already supports both SQLite and PostgreSQL dialects. The missing pieces are dependency provisioning, documented configuration, backend-agnostic tests, and CI coverage for PostgreSQL.

## Goals / Non-Goals

**Goals:**
- Support PostgreSQL as an optional backend via `CODEX_LB_DATABASE_URL`.
- Preserve SQLite as the default backend and default documentation path.
- Validate PostgreSQL in CI to avoid regression.

**Non-Goals:**
- Replacing SQLite-specific integrity/recovery tooling with cross-database recovery.
- Adding MySQL/MariaDB or other SQL dialect support in this change.
- Making PostgreSQL mandatory for local development.

## Decisions

1. **Keep SQLite default**
   - Continue defaulting `database_url` to SQLite so local `uvx codex-lb` remains zero-config.

2. **Add PostgreSQL driver at runtime dependency layer**
   - Include `asyncpg` in runtime dependencies so a Postgres DSN works without extra package installation steps.

3. **Make tests backend-selectable by env**
   - Replace hard-coded SQLite URL assignment in test bootstrap with `os.environ.setdefault(...)`.
   - This keeps SQLite default tests and enables PostgreSQL jobs via environment overrides.

4. **Add dedicated PostgreSQL CI test job**
   - Use a PostgreSQL service container in GitHub Actions and run pytest with a Postgres DSN.

## Risks / Trade-offs

- **[Risk] Longer CI runtime**: running both SQLite and PostgreSQL test jobs increases execution time.
- **[Risk] Dialect-specific test assumptions**: tests may accidentally rely on SQLite behavior and fail on PostgreSQL.
- **[Trade-off] Runtime dependency size**: adding `asyncpg` slightly increases install footprint, in exchange for simpler out-of-box PostgreSQL support.

## Migration Plan

1. Add dependency + lockfile update.
2. Update test bootstrap and add PostgreSQL import coverage.
3. Add PostgreSQL CI job.
4. Update configuration docs/examples.
5. Validate lint/tests.

## Open Questions

- Whether to move `asyncpg` into an optional dependency group in a later change while preserving easy onboarding.
