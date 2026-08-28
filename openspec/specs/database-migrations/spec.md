# database-migrations Specification

## Purpose

Define migration, drift detection, and Alembic governance contracts so deployments fail closed on schema mismatch.
## Requirements
### Requirement: Alembic as migration source of truth

The system SHALL use Alembic as the only runtime migration mechanism and SHALL NOT execute custom migration runners. Dashboard settings schema changes, including weekly pace working days, MUST be represented by Alembic revisions and ORM metadata so startup drift detection can verify them.

#### Scenario: Application startup performs Alembic migration

- **WHEN** the application starts
- **THEN** it runs Alembic upgrade to `head`
- **AND** it applies fail-fast behavior according to configuration

#### Scenario: Dashboard settings migration persists weekly pace working days

- **WHEN** migrations run to head on an existing install
- **THEN** `dashboard_settings` contains a non-null `weekly_pace_working_days` column
- **AND** existing rows default to `0,1,2,3,4,5,6`

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

The database SHALL persist global warm-up settings, per-account opt-in, warm-up attempt history, and request-log source metadata. Global warm-up settings SHALL include a non-null exhausted-threshold percent used by reset-confirmed limit warm-up candidate selection.

#### Scenario: Warm-up attempt is unique per reset
- **WHEN** an attempt is stored for an account, window, and reset timestamp
- **THEN** the database enforces uniqueness for that account/window/reset tuple

#### Scenario: Existing installs remain disabled
- **WHEN** an existing database is migrated
- **THEN** global warm-up is disabled
- **AND** all existing accounts remain opted out
- **AND** the exhausted-threshold percent defaults to `99.0`

#### Scenario: Warm-up request logs remain separable from user traffic
- **WHEN** a warm-up request is logged
- **THEN** the request log records a source value that allows account usage summaries to exclude internal warm-up traffic

### Requirement: New request-log failure metadata migration MUST be linear on current heads

The new request-log failure metadata migration MUST be ordered after the merge
revision that joins parallel
`20260426_000000_add_dashboard_relative_availability_settings` and
`20260525_000000_add_usage_raw_window_latest_index` when a deployment upgrades
from current `main`.

#### Scenario: Migration check does not report multiple heads

- **WHEN** Alembic migration check runs on a database that includes current
  upstream `main` history
- **THEN** the check passes without `MultipleHeads` for request-log metadata migration
- **AND** the migration path remains `... -> 20260601_... -> 20260526_...`

### Requirement: Legacy free-account usage-history rows are isolated from monthly semantics

The database migration path SHALL rename legacy free-account `usage_history.window` labels before normalized monthly-only free-account rows are written.

#### Scenario: Free-account legacy primary and secondary rows are renamed
- **WHEN** the migration runs against `usage_history` rows joined to accounts whose current `plan_type` is `free`
- **THEN** rows whose `window` is `primary` are rewritten to `old-primary`
- **AND** rows whose `window` is `secondary` are rewritten to `old-secondary`

#### Scenario: Non-free account rows remain unchanged
- **WHEN** the migration runs against `usage_history` rows joined to accounts whose current `plan_type` is not `free`
- **THEN** existing `primary` and `secondary` labels remain unchanged

### Requirement: Accounts have server-owned Codex installation ids

The `accounts` table MUST store a non-null `codex_installation_id` for every
account. New account rows MUST receive a generated UUID value. Existing account
rows MUST be backfilled during migration.

#### Scenario: Existing accounts are backfilled

- **GIVEN** an existing database has account rows without
  `codex_installation_id`
- **WHEN** migrations upgrade to the new revision
- **THEN** each existing account row has a non-empty UUID

#### Scenario: New accounts receive an installation id

- **WHEN** a new account row is created by the application
- **THEN** `codex_installation_id` is populated without trusting client input

### Requirement: Request log client IP migration is nullable and indexed

The database migration MUST add nullable `request_logs.client_ip` storage and an index for client-IP request-log lookup. The migration MUST be safe to run against databases where the table is absent or the column/index already exists.

#### Scenario: Upgrade adds client IP storage

- **WHEN** the migration is applied to a database containing `request_logs`
- **THEN** `request_logs.client_ip` exists and is nullable
- **AND** an index exists for `request_logs.client_ip`

#### Scenario: Downgrade removes client IP storage

- **WHEN** the migration is downgraded
- **THEN** the `client_ip` index and column are removed when present

### Requirement: Staggered idle warm-up idle threshold column

The `dashboard_settings` table MUST include a `limit_warmup_idle_threshold_percent` column of type `Float`, nullable `False`, with a server default of `1.0`. This column stores the operator-configurable idle threshold for the staggered idle warm-up path, independent from the regular warm-up's `limit_warmup_exhausted_threshold_percent`.

#### Scenario: Column exists after migration

- **GIVEN** the database has been migrated to the latest revision
- **WHEN** the schema is inspected
- **THEN** the `dashboard_settings` table includes `limit_warmup_idle_threshold_percent` as a non-null `Float` column
- **AND** the default value is `1.0`

#### Scenario: Existing rows get the default value

- **GIVEN** a `dashboard_settings` row exists before the migration
- **WHEN** the migration adds the column
- **THEN** the existing row's `limit_warmup_idle_threshold_percent` is `1.0`

### Requirement: Account schema preserves workspace membership metadata

The database schema SHALL store optional workspace and seat metadata for accounts without rewriting existing account primary keys.

#### Scenario: Existing account ids remain stable

- **WHEN** the workspace identity migration is applied
- **THEN** existing `accounts.id` values are not modified
- **AND** nullable `workspace_id`, `workspace_label`, and `seat_type` columns are available

### Requirement: Request-log archive lookup schema
The database schema SHALL preserve a nullable archive lookup id on request logs so dashboard archive lookups can remain distinct from response-id continuity lookup.

#### Scenario: Request-log archive lookup column exists after migration
- **WHEN** migrations run to head
- **THEN** `request_logs` contains a nullable `archive_request_id` column
- **AND** existing request-log rows without the column value remain valid

### Requirement: Dashboard settings persistence

The database SHALL persist dashboard settings, including weekly pace working days and the weekly pace gap smoothing window.

#### Scenario: Existing installs receive weekly pace smoothing default
- **WHEN** an existing database is migrated
- **THEN** `dashboard_settings.weekly_pace_smoothing_minutes` exists
- **AND** existing rows use a default smoothing window of 30 minutes

### Requirement: SQLite pre-migration backups use online snapshots

When startup creates a pre-migration backup for a SQLite database, it SHALL use
SQLite's online backup mechanism rather than copying only the main database
file. The backup SHALL include committed rows that are currently resident in WAL
state and SHALL produce a standalone SQLite database file without requiring a
sidecar WAL file.

#### Scenario: WAL-resident rows are present in the backup

- **GIVEN** a file-backed SQLite database has WAL mode enabled
- **AND** committed rows are still present in the source database WAL
- **WHEN** the pre-migration backup is created
- **THEN** those rows are queryable from the backup database
- **AND** the backup passes SQLite integrity checking

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

### Requirement: Migration CLI distinguishes omitted and empty targets

The `app.db.migrate` / `codex-lb-db` CLI SHALL use the settings-derived database URL only when `--db-url` is omitted. If `--db-url` is explicitly supplied as an exact empty string, the CLI MUST terminate with an argument error before resolving or opening a settings-derived database target. This validation MUST apply to every supported migration subcommand: `upgrade`, `current`, `check`, `wait-for-head`, `wait-for-connection`, and `stamp`.

#### Scenario: Explicit empty target is rejected before side effects

- **GIVEN** settings would resolve a valid database target
- **WHEN** any supported migration subcommand is invoked with `--db-url ""`
- **THEN** the CLI exits nonzero with an argument-validation error
- **AND** it does not select, connect to, create, inspect, migrate, or stamp the settings-derived target

#### Scenario: Omitted target retains the settings fallback

- **GIVEN** settings resolve a valid database target
- **WHEN** a supported migration subcommand is invoked without `--db-url`
- **THEN** the CLI uses the settings-derived database target

### Requirement: Capability lineage uses one additive opaque-marker table

The migration MUST descend from the current single Alembic head and create one
`capability_lineage_markers` table containing only an opaque SHA-256 marker
primary key plus creation and last-seen timestamps. It MUST NOT modify existing
sticky-session, account, usage, quota, request-log, or durable-bridge columns or
foreign keys, and MUST NOT backfill historical rows.

#### Scenario: Upgrade creates an empty marker table
- **WHEN** a database at the previous head upgrades to the new head
- **THEN** the marker table exists with its primary-key uniqueness contract
- **AND** no existing application table is scanned or rewritten for backfill

#### Scenario: Downgrade removes only the marker table
- **WHEN** the migration is downgraded to its parent revision
- **THEN** only `capability_lineage_markers` is removed
- **AND** existing application data remains unchanged

#### Scenario: Migration graph remains single-head
- **WHEN** the repository migration graph is inspected after this change
- **THEN** it has exactly one head containing the marker-table revision

### Requirement: SQLite maintenance releases file handles before filesystem mutation

Synchronous SQLite maintenance operations MUST explicitly close every native
connection they open after completing or rolling back its transaction. A
pre-migration backup MUST release its source and destination connections before
retention deletes an older snapshot. Recovery with `--replace` MUST release
connections used for integrity checking, dump export, and dump import before it
renames either the source database or recovered output. Correctness MUST NOT
depend on garbage collection or interpreter object-finalization timing.

#### Scenario: Backup retention deletes an old snapshot on Windows

- **GIVEN** SQLite pre-migration backups have reached their retention limit
- **WHEN** a new online snapshot is complete and retention deletes the oldest
  snapshot
- **THEN** every connection opened for the completed snapshot is explicitly
  closed before deletion
- **AND** backup rotation succeeds on platforms that prohibit deleting an open
  database file

#### Scenario: Recovery replaces a database on Windows

- **GIVEN** a file-backed SQLite database is recovered through the CLI with
  `--replace`
- **WHEN** dump export and import complete
- **THEN** the integrity-check, source, and output connections are explicitly
  closed before either database file is renamed
- **AND** the original is preserved under the corrupt-backup name while the
  recovered database is moved into the original path

### Requirement: Alembic Config escapes percent characters in the SQLAlchemy URL

When the application builds an Alembic `Config` for migration inspection or upgrade (`_build_alembic_config`), any `%` in the SQLAlchemy URL MUST be escaped to `%%` before being stored via `set_main_option`. Alembic stores option values in a `configparser` using `BasicInterpolation`, which treats a bare `%` as interpolation syntax; a percent-encoded Windows path (`C%3A%5CUsers%5C...`) otherwise raises `ValueError: invalid interpolation syntax` during startup. `get_main_option` decodes `%%` back to `%`, so the URL handed to SQLAlchemy is unchanged.

#### Scenario: Windows path does not crash migration inspection

- **GIVEN** the default SQLite URL on Windows, percent-encoded by `URL.render_as_string()` into `sqlite:///C%3A%5CUsers%5C...%5Cstore.db`
- **WHEN** the Alembic `Config` is built for migration inspection
- **THEN** no `ValueError: invalid interpolation syntax` is raised
- **AND** `get_main_option("sqlalchemy.url")` returns the normalized sync URL whose path is the decoded Windows filesystem path (`sqlite:///C:\Users\...\store.db`), because `to_sync_database_url` normalizes recognizable SQLAlchemy-rendered Windows SQLite URLs before the value is stored in the Alembic `Config`

#### Scenario: Round-trip preserves an already-encoded percent

- **GIVEN** a path that already contains a percent-encoded `%` (rendered as `%25`)
- **WHEN** the escape turns it into `%%25` and `get_main_option` decodes it
- **THEN** the value SQLAlchemy receives decodes back to `%25`, i.e. the original URL is preserved exactly

#### Scenario: Non-Windows URLs are unaffected

- **GIVEN** a SQLite or PostgreSQL URL whose path contains no `%`
- **WHEN** the escape and decode round-trip is applied
- **THEN** the URL is unchanged and migration behavior is identical to before

