## MODIFIED Requirements

### Requirement: Alembic startup uses the dedicated migration DSN
The system SHALL run startup migrations, revision inspection, and schema drift checks against the resolved migration DSN rather than the runtime pooled DSN when they differ.

#### Scenario: Dedicated migration DSN is configured
- **WHEN** `CODEX_LB_DATABASE_MIGRATION_URL` is set
- **THEN** startup migrations, Alembic CLI defaults, and drift checks use that DSN
- **AND** runtime ORM sessions continue using `CODEX_LB_DATABASE_URL`

### Requirement: SQLite runtime backup flow is not part of PostgreSQL startup
The system SHALL NOT perform SQLite integrity checks or pre-migration backups in the PostgreSQL runtime startup path.

#### Scenario: Runtime starts on Neon PostgreSQL
- **WHEN** the configured backend is PostgreSQL
- **THEN** startup migration flow skips SQLite-specific backup and integrity tooling
