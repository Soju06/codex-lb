# database-migrations Delta

## ADDED Requirements

### Requirement: Startup migrations are mutually exclusive across processes

The system SHALL serialize schema upgrades and stamps across all processes sharing a database using a backend-appropriate cross-process mutex: a PostgreSQL session-level advisory lock held on a dedicated connection for the full upgrade sequence, or an exclusive write transaction on a sentinel SQLite file adjacent to a file-backed SQLite database (no-op for in-memory SQLite). After acquiring the mutex, the upgrader MUST re-inspect migration state and MUST skip applying revisions when the target is head and the schema is already at head with no legacy bootstrap or revision remap pending, completing startup successfully. Waiting for the mutex MUST be bounded by `database_migration_lock_timeout_seconds` (default 300); on timeout the system SHALL raise an explicit error naming the migration lock and the timeout setting, honoring `database_migrations_fail_fast` on the startup path.

#### Scenario: Two processes upgrade a fresh database concurrently

- **WHEN** two processes concurrently run upgrade to head against the same fresh database
- **THEN** each pending revision is applied exactly once
- **AND** both processes report the head revision
- **AND** neither process fails with duplicate-object errors

#### Scenario: A process starts while a peer is migrating

- **GIVEN** a peer process holds the migration lock while upgrading to head
- **WHEN** the peer completes to head and releases the lock
- **THEN** the waiting process proceeds without applying revisions
- **AND** it logs that the database is already at head

#### Scenario: Lock wait exceeds the timeout

- **GIVEN** another process holds the migration lock for longer than `database_migration_lock_timeout_seconds`
- **WHEN** an upgrade or stamp attempts to acquire the lock
- **THEN** it fails with an error that names the migration lock and the `database_migration_lock_timeout_seconds` setting

### Requirement: Schema newer than build is reported distinctly from schema behind head

Migration state inspection SHALL classify `alembic_version` revisions that are neither present in the local Alembic script directory nor legacy-remappable as schema-ahead, and startup diagnostics MUST report schema-ahead databases as newer than or unknown to the running build — directing the operator to deploy a matching or newer image or downgrade the schema — rather than claiming the schema is behind Alembic head. Exact-head fail-closed gating itself is unchanged.

#### Scenario: Startup migration disabled against a newer schema

- **GIVEN** `database_migrate_on_startup=false`
- **AND** `alembic_version` contains a revision unknown to the running build
- **WHEN** the application starts
- **THEN** startup fails with an error stating the schema revision is not known to this build and directing the operator to deploy a matching or newer image or downgrade the schema
- **AND** the error does not claim the schema is behind Alembic head

#### Scenario: Startup migration enabled against a newer schema

- **GIVEN** startup migration is enabled
- **AND** `alembic_version` contains a revision unknown to the running build
- **WHEN** the upgrade runs
- **THEN** it fails with the ahead-specific guidance rather than a generic unsupported-revision remap error
