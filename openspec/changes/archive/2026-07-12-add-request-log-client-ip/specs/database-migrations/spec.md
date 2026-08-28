## ADDED Requirements

### Requirement: Request log client IP migration is nullable and indexed

The database migration MUST add nullable `request_logs.client_ip` storage and an index for client-IP request-log lookup. The migration MUST be safe to run against databases where the table is absent or the column/index already exists.

#### Scenario: Upgrade adds client IP storage

- **WHEN** the migration is applied to a database containing `request_logs`
- **THEN** `request_logs.client_ip` exists and is nullable
- **AND** an index exists for `request_logs.client_ip`

#### Scenario: Downgrade removes client IP storage

- **WHEN** the migration is downgraded
- **THEN** the `client_ip` index and column are removed when present
