# database-migrations Specification

## Purpose
TBD - created by archiving change alembic-hard-cut-migrations. Update Purpose after archive.
## Requirements
### Requirement: Alembic as migration source of truth

The system SHALL use Alembic as the only runtime migration mechanism and SHALL NOT execute custom migration runners.

#### Scenario: Application startup performs Alembic migration

- **WHEN** the application starts
- **THEN** it runs Alembic upgrade to `head`
- **AND** it applies fail-fast behavior according to configuration

### Requirement: Legacy migration history bootstrap

The system SHALL automatically bootstrap legacy `schema_migrations` history into Alembic revision state when `alembic_version` is missing.

#### Scenario: Legacy history exists

- **GIVEN** `schema_migrations` exists and `alembic_version` does not exist
- **WHEN** startup migration runs
- **THEN** the system stamps the highest contiguous known legacy revision
- **AND** continues with Alembic upgrade to `head`

### Requirement: Idempotent migration behavior across DB states

The migration chain SHALL be idempotent for fresh databases and partially migrated legacy databases.

#### Scenario: Migration rerun

- **WHEN** startup migration runs repeatedly on the same database
- **THEN** schema state remains stable
- **AND** the current Alembic revision remains `head`

### Requirement: Automatic SQLite pre-migration backup

The system SHALL create a SQLite backup before applying startup migrations when an upgrade is needed.

#### Scenario: Startup detects pending migration on SQLite

- **GIVEN** the configured database is a SQLite file
- **AND** startup migration is enabled
- **AND** migration state indicates upgrade is required
- **WHEN** startup migration begins
- **THEN** the system creates a pre-migration backup file
- **AND** enforces configured retention on backup files

### Requirement: Migration drift guard in CI

The project SHALL fail CI when ORM metadata and migrated schema diverge.

#### Scenario: Drift check run

- **WHEN** CI executes migration checks
- **THEN** it upgrades a temporary database to `head`
- **AND** runs a drift check command
- **AND** fails if drift is detected

