# database-migrations Specification

## Purpose

See context docs for background.
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

### Requirement: Peer fallback targets are migrated

The system SHALL add persistent peer fallback target storage through Alembic so dashboard-managed peer fallback targets are available on supported database backends.

#### Scenario: Migration creates peer fallback target storage

- **WHEN** the database is upgraded to the migration containing peer fallback target storage
- **THEN** the schema contains a `peer_fallback_targets` table with stable target identifiers, normalized base URLs, enabled flags, and timestamps
- **AND** the base URL is constrained to be unique

### Requirement: API key peer fallback URLs are migrated

The system SHALL add persistent API key peer fallback URL storage through Alembic so each API key can be associated with zero or more peer codex-lb base URLs.

#### Scenario: Migration creates API key peer fallback URL storage

- **WHEN** the database is upgraded to the migration containing API key peer fallback URL storage
- **THEN** the schema contains an `api_key_peer_fallback_urls` table linking API keys to peer fallback base URLs
- **AND** deleting an API key removes its peer fallback URL rows
