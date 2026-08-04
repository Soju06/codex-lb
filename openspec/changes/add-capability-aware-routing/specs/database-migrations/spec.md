## ADDED Requirements

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
