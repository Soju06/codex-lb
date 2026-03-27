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

### Requirement: Startup schema drift guard

After startup migrations report success, the system SHALL verify that the live database schema matches ORM metadata before the application continues normal startup. If drift remains, the system SHALL surface explicit drift details and SHALL apply fail-fast behavior according to configuration instead of silently serving with a divergent schema.

#### Scenario: Startup detects drift with fail-fast enabled

- **GIVEN** startup migrations complete without raising an Alembic upgrade error
- **AND** post-migration schema drift check returns one or more diffs
- **AND** `database_migrations_fail_fast=true`
- **WHEN** application startup continues
- **THEN** the system raises an explicit startup error that includes schema drift context
- **AND** the application does not continue normal startup

#### Scenario: Startup detects drift with fail-fast disabled

- **GIVEN** startup migrations complete without raising an Alembic upgrade error
- **AND** post-migration schema drift check returns one or more diffs
- **AND** `database_migrations_fail_fast=false`
- **WHEN** application startup continues
- **THEN** the system logs the drift details as an error
- **AND** it does not silently suppress the drift context

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

### Requirement: Durable response snapshot continuity storage

Startup migrations SHALL create and preserve the durable storage required for replaying `previous_response_id` across bridge loss and restart. This storage SHALL include a `response_snapshots` table with caller-scoping metadata and serialized request/response continuity data.

#### Scenario: Startup creates response snapshot storage

- **WHEN** startup migrations upgrade a database that does not yet include response continuity storage
- **THEN** the migrated schema includes `response_snapshots`
- **AND** that table includes `response_id`, `parent_response_id`, `account_id`, `api_key_id`, `model`, `input_items_json`, `response_json`, and `created_at`

#### Scenario: Startup repairs partial response snapshot storage

- **WHEN** startup migrations encounter an existing `response_snapshots` table missing the caller-scoping column or continuity index
- **THEN** the migration adds the missing `api_key_id` column and required continuity index without requiring manual intervention

### Requirement: Migration policy and drift guard in CI

The project SHALL fail CI when migration policy is violated or ORM metadata and migrated schema diverge.

#### Scenario: CI migration check run

- **WHEN** CI executes migration checks
- **THEN** it upgrades a temporary database to `head`
- **AND** runs a unified migration check command
- **AND** fails if policy violations or drift are detected
