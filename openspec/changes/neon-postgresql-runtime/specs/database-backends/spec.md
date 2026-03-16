## MODIFIED Requirements

### Requirement: PostgreSQL on Neon is the required runtime backend
The service MUST require `CODEX_LB_DATABASE_URL` to be set to a PostgreSQL SQLAlchemy async DSN and MUST fail fast when it is missing.

#### Scenario: Runtime starts without a database URL
- **WHEN** the service starts without `CODEX_LB_DATABASE_URL`
- **THEN** settings initialization fails with an explicit configuration error

#### Scenario: Runtime starts with a PostgreSQL URL
- **WHEN** `CODEX_LB_DATABASE_URL` is set to `postgresql+asyncpg://...`
- **THEN** service startup uses PostgreSQL for ORM operations
- **AND** it does not require SQLite path handling or startup validation

### Requirement: Runtime migrations use a dedicated migration URL
The service MUST accept `CODEX_LB_DATABASE_MIGRATION_URL` as the canonical DSN for Alembic and startup migrations.

#### Scenario: Migration URL is not set explicitly
- **WHEN** `CODEX_LB_DATABASE_MIGRATION_URL` is unset
- **THEN** the service uses `CODEX_LB_DATABASE_URL` as the migration DSN

#### Scenario: Startup migrations are enabled without any migration DSN
- **WHEN** startup migrations are enabled and no resolved migration DSN is available
- **THEN** startup fails fast with an explicit configuration error

### Requirement: Test suite requires explicit PostgreSQL configuration
The test bootstrap MUST honor `CODEX_LB_TEST_DATABASE_URL` as the runtime DSN and `CODEX_LB_TEST_DATABASE_MIGRATION_URL` as the migration DSN without defaulting to SQLite.

#### Scenario: Tests start without PostgreSQL env
- **WHEN** tests are run without `CODEX_LB_TEST_DATABASE_URL`
- **THEN** test bootstrap fails with an explicit configuration error
