# Database Backends

## Purpose

Define supported database backends and required runtime database behavior for codex-lb persistence.

## Requirements

### Requirement: PostgreSQL on Neon is the required runtime backend
The service MUST require `CODEX_LB_DATABASE_URL` to be set to a PostgreSQL SQLAlchemy async DSN when the application starts.

#### Scenario: No database URL configured
- **WHEN** the service starts without `CODEX_LB_DATABASE_URL`
- **THEN** settings initialization fails with an explicit configuration error

#### Scenario: PostgreSQL URL configured
- **WHEN** `CODEX_LB_DATABASE_URL` is set to `postgresql+asyncpg://...`
- **THEN** service startup uses PostgreSQL for ORM operations
- **AND** it does not perform SQLite-specific startup validation or file-path setup

### Requirement: Runtime migrations use a dedicated migration URL
The service MUST accept a dedicated PostgreSQL DSN via `CODEX_LB_DATABASE_MIGRATION_URL` for Alembic and startup migration execution.

#### Scenario: Dedicated migration URL configured
- **WHEN** `CODEX_LB_DATABASE_MIGRATION_URL` is set to `postgresql+asyncpg://...`
- **THEN** startup migration and Alembic execution use that DSN
- **AND** runtime ORM sessions still use `CODEX_LB_DATABASE_URL`

#### Scenario: Migration URL omitted
- **WHEN** `CODEX_LB_DATABASE_MIGRATION_URL` is not set
- **THEN** the service uses `CODEX_LB_DATABASE_URL` as the migration DSN

### Requirement: Test suite requires explicit PostgreSQL backend configuration
The test bootstrap MUST allow callers to override runtime and migration DSNs via `CODEX_LB_TEST_DATABASE_URL` and `CODEX_LB_TEST_DATABASE_MIGRATION_URL` and MUST NOT silently default to SQLite.

#### Scenario: CI sets PostgreSQL URLs
- **WHEN** CI sets `CODEX_LB_TEST_DATABASE_URL` and `CODEX_LB_TEST_DATABASE_MIGRATION_URL`
- **THEN** tests run against PostgreSQL without modifying test code

#### Scenario: Test run without URL override
- **WHEN** tests are run without `CODEX_LB_TEST_DATABASE_URL`
- **THEN** test bootstrap fails with an explicit configuration error

### Requirement: ORM enums persist schema string values
ORM enum columns backed by named PostgreSQL enums MUST persist the lowercase string values defined by the schema and migrations, not Python enum member names.

#### Scenario: SQLAlchemy binds account and API key enums
- **WHEN** the ORM metadata is built for `Account.status`, `ApiKeyLimit.limit_type`, and `ApiKeyLimit.limit_window`
- **THEN** each SQLAlchemy enum type exposes the same lowercase string values used by migrations and persisted rows
