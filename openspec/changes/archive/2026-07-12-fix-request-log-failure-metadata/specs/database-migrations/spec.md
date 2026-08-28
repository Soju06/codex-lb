## ADDED Requirements

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
