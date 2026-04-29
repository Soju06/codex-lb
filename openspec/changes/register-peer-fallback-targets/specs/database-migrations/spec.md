## ADDED Requirements

### Requirement: Peer fallback targets are migrated

The system SHALL add persistent peer fallback target storage through Alembic so dashboard-managed peer fallback targets are available on supported database backends.

#### Scenario: Migration creates peer fallback target storage

- **WHEN** the database is upgraded to the migration containing peer fallback target storage
- **THEN** the schema contains a `peer_fallback_targets` table with stable target identifiers, normalized base URLs, enabled flags, and timestamps
- **AND** the base URL is constrained to be unique
