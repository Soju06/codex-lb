## 1. Alembic Infrastructure

- [x] 1.1 Add Alembic runtime integration under `app/db/alembic/`
- [x] 1.2 Add revision chain `000_base_schema` through `008_add_api_keys`
- [x] 1.3 Add DB migration command module `app/db/migrate.py`

## 2. Startup Cutover

- [x] 2.1 Replace custom migration execution in `app/db/session.py` with Alembic startup migration
- [x] 2.2 Preserve SQLite integrity checks and fail-fast behavior
- [x] 2.3 Add legacy `schema_migrations` bootstrap to alembic stamp path

## 3. Legacy Removal

- [x] 3.1 Remove `app/db/migrations` custom runner and version scripts
- [x] 3.2 Remove runtime references to custom migration APIs

## 4. Dependencies & Tooling

- [x] 4.1 Add `alembic` dependency
- [x] 4.2 Add PostgreSQL driver parity dependencies (`asyncpg`, `psycopg[binary]`)
- [x] 4.3 Add migration CLI entrypoint `codex-lb-db`

## 5. Tests

- [x] 5.1 Update migration integration tests to validate Alembic path
- [x] 5.2 Add tests for legacy bootstrap stamping
- [x] 5.3 Add tests for unknown legacy entries handling

## 6. Automation Hardening

- [x] 6.1 Add migration state inspection and schema drift check command (`check`)
- [x] 6.2 Add SQLite pre-migration backup with retention
- [x] 6.3 Add CI migration drift check job
- [x] 6.4 Add Docker startup pre-upgrade entrypoint
