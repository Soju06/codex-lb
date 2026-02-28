# database-migrations Specification

## Purpose

Define codex-lb database migration behavior so schema upgrades are deterministic, operationally safe, and CI-enforced under Alembic.

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

### Requirement: Automatic remap for legacy Alembic revision IDs

The system SHALL automatically remap known legacy Alembic revision IDs in `alembic_version` to timestamp-based revision IDs before migration upgrade.

#### Scenario: Startup sees known legacy Alembic revision IDs

- **WHEN** startup migration finds known legacy IDs in `alembic_version`
- **THEN** it replaces them with mapped timestamp-based revision IDs
- **AND** proceeds with Alembic upgrade to `head`

#### Scenario: Startup sees unsupported Alembic revision ID

- **WHEN** startup migration finds IDs that are neither current revisions nor known legacy IDs
- **THEN** it fails fast with an explicit error requiring operator intervention

### Requirement: Alembic revision naming policy

All Alembic revision IDs SHALL match `^\d{8}_\d{6}_[a-z0-9_]+$` and each migration filename SHALL be `<revision>.py`.

#### Scenario: Migration policy check validates naming

- **WHEN** migration policy checks run
- **THEN** every revision ID matches the timestamp-based naming format
- **AND** every migration filename matches its revision ID

### Requirement: Single-head convergence at merge gate

The project SHALL converge to a single Alembic head at merge/release gates.

#### Scenario: Policy check detects multiple heads

- **WHEN** migration policy check evaluates the revision graph
- **THEN** it fails if more than one head exists
- **AND** requires a merge revision before merge/release

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

### Requirement: Migration policy and drift guard in CI

The project SHALL fail CI when migration policy is violated or ORM metadata and migrated schema diverge.

#### Scenario: CI migration check run

- **WHEN** CI executes migration checks
- **THEN** it upgrades a temporary database to `head`
- **AND** runs a unified migration check command
- **AND** fails if policy violations or drift are detected
