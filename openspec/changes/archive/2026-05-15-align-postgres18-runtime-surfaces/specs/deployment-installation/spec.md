## ADDED Requirements

### Requirement: Helm PostgreSQL helpers track the PostgreSQL runtime major

The Helm chart MUST use PostgreSQL client images on the same major runtime
baseline as bundled database coverage for migration and database initialization
helpers.

#### Scenario: Hook helper images use PostgreSQL 18

- **WHEN** the Helm chart renders the migration job with bundled PostgreSQL
  enabled
- **THEN** the wait-for-db init container uses a PostgreSQL 18 image
- **AND** the database initialization job uses a PostgreSQL 18 image
