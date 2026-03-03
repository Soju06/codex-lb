## 1. Dependency and configuration support

- [x] 1.1 Add `asyncpg` dependency and refresh `uv.lock`
- [x] 1.2 Add optional PostgreSQL DSN examples while keeping SQLite default

## 2. Test compatibility across backends

- [x] 2.1 Refactor test DB bootstrap so it respects externally provided `CODEX_LB_DATABASE_URL`
- [x] 2.2 Add/extend session import tests for a PostgreSQL URL

## 3. CI validation

- [x] 3.1 Add PostgreSQL-backed pytest job in CI
- [x] 3.2 Keep existing SQLite-backed pytest job as default path

## 4. OpenSpec SSOT updates

- [x] 4.1 Add `database-backends` capability spec with normative requirements
- [x] 4.2 Add `database-backends` context documentation with examples and constraints
