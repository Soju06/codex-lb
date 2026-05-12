## ADDED Requirements

### Requirement: Merge upstream Alembic graph into one safe head

The upstream Alembic revisions for dashboard session lifetime, API-key limit reset indexing, background database pool behavior, and request-log changes MUST be reconciled with the local migration chain to produce a single valid head. Legacy revision remapping MUST NOT skip schema changes that are required by local Platform fallback, durable bridge, request-log, or sticky-session behavior.

#### Scenario: Existing local database upgrades through merged head

- **GIVEN** a database created from current `main`
- **WHEN** the merged application upgrades Alembic to `head`
- **THEN** the migration succeeds
- **AND** durable bridge, sticky-session, request-log, dashboard-session, and API-key limit reset schema elements are present

#### Scenario: Legacy remap does not mark missing schema as applied

- **GIVEN** a database records a legacy revision known to the remap table
- **WHEN** startup remaps and upgrades the revision
- **THEN** the upgrade path still applies any newer current migrations whose schema is not already present

### Requirement: Migration conflict resolution is tested against SQLite and PostgreSQL-compatible paths

The merge MUST include migration tests that cover the merged graph and repository behavior for both local SQLite development and PostgreSQL-compatible production usage.

#### Scenario: Migration test suite validates merged graph

- **WHEN** migration-focused tests run after conflict resolution
- **THEN** they validate a single Alembic head and representative upgrade behavior
- **AND** any PostgreSQL-only behavior is exercised with a Podman-backed database when the host environment is insufficient
