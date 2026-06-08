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

### Requirement: Request-log account deletion preserves historical rows

The database schema SHALL preserve historical `request_logs` rows when their parent account is deleted. The schema MUST support a nullable request-log soft-delete marker and MUST NOT use a cascading account foreign key that deletes request-log history.

#### Scenario: Request-log soft-delete schema exists after migration

- **WHEN** migrations run to head
- **THEN** `request_logs` contains a nullable `deleted_at` column
- **AND** the dashboard request-log list path has an index that supports filtering non-deleted rows latest-first

#### Scenario: Request-log account foreign key no longer cascades

- **WHEN** migrations run to head
- **THEN** the `request_logs.account_id -> accounts.id` foreign key uses `ON DELETE SET NULL`
- **AND** deleting an account at the database level does not delete matching request-log rows

### Requirement: Limit warm-up persistence

The database SHALL persist global warm-up settings, per-account opt-in, warm-up attempt history, and request-log source metadata.

#### Scenario: Warm-up attempt is unique per reset
- **WHEN** an attempt is stored for an account, window, and reset timestamp
- **THEN** the database enforces uniqueness for that account/window/reset tuple

#### Scenario: Existing installs remain disabled
- **WHEN** an existing database is migrated
- **THEN** global warm-up is disabled
- **AND** all existing accounts remain opted out

#### Scenario: Warm-up request logs remain separable from user traffic
- **WHEN** a warm-up request is logged
- **THEN** the request log records a source value that allows account usage summaries to exclude internal warm-up traffic

