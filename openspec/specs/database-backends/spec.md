# Database Backends

## Purpose

Define supported database backends and default backend behavior for codex-lb persistence.

## Requirements

### Requirement: SQLite remains the default backend
The service MUST default `CODEX_LB_DATABASE_URL` to a SQLite DSN when no explicit database URL is provided.

#### Scenario: No database URL configured
- **WHEN** the service starts without `CODEX_LB_DATABASE_URL`
- **THEN** it initializes and runs against the default SQLite database path

### Requirement: PostgreSQL is supported as an optional backend
The service MUST accept a PostgreSQL SQLAlchemy async DSN (`postgresql+asyncpg://...`) via `CODEX_LB_DATABASE_URL` and initialize SQLAlchemy session/engine wiring without requiring SQLite-specific paths.

#### Scenario: PostgreSQL URL configured
- **WHEN** `CODEX_LB_DATABASE_URL` is set to `postgresql+asyncpg://...`
- **THEN** service startup uses PostgreSQL for ORM operations and migration execution

### Requirement: Test suite supports backend selection
The test bootstrap MUST allow callers to override `CODEX_LB_DATABASE_URL` via environment and MUST default to SQLite when no override is provided.

#### Scenario: CI sets PostgreSQL URL
- **WHEN** CI sets `CODEX_LB_DATABASE_URL` to a PostgreSQL DSN
- **THEN** tests run against PostgreSQL without modifying test code

#### Scenario: Local test run without URL override
- **WHEN** tests are run without setting `CODEX_LB_DATABASE_URL`
- **THEN** tests run against a temporary SQLite database

### Requirement: CI validates both default and optional backends
CI MUST keep SQLite-backed tests as the default path and MUST run an additional PostgreSQL-backed test job.

#### Scenario: CI workflow execution
- **WHEN** CI runs on push or pull request
- **THEN** at least one pytest job runs with SQLite and another pytest job runs with PostgreSQL
