# database-migrations Specification

## Purpose

Define migration, drift detection, and Alembic governance contracts so deployments fail closed on schema mismatch.

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

### Requirement: Legacy revision remap preserves downstream migrations

Known legacy Alembic revision IDs SHALL be remapped to a valid current revision without marking any newer current migration as already applied unless that migration's schema changes are guaranteed to exist in the database.

#### Scenario: Legacy import-default revision still applies bridge migrations

- **GIVEN** a database records legacy revision `20260410_020000_restore_import_without_overwrite_default_false`
- **AND** durable HTTP bridge tables are not present yet
- **WHEN** startup auto-remaps the legacy revision and upgrades to head
- **THEN** the upgrade path still applies the durable HTTP bridge migrations
- **AND** the database contains `http_bridge_sessions` and `http_bridge_session_aliases`

### Requirement: Compatibility downgrades preserve preexisting columns

Downgrades for additive compatibility migrations SHALL remove indexes or constraints introduced by that migration without dropping nullable columns that may have existed before the migration ran.

#### Scenario: Request log session id survives response lookup downgrade

- **GIVEN** `request_logs.session_id` exists before the response lookup index migration is downgraded
- **WHEN** the response lookup index migration is downgraded
- **THEN** the migration drops its lookup indexes
- **AND** it preserves `request_logs.session_id`
