## MODIFIED Requirements

### Requirement: HTTP bridge instance ownership remains deterministic without unnecessary SQLite coordination

The service MUST avoid unnecessary database-backed bridge ring coordination when a deployment can safely operate with a static single-instance ring.

#### Scenario: SQLite-backed deployment uses static bridge ring membership
- **WHEN** the deployment uses a SQLite database
- **AND** the HTTP responses session bridge is enabled
- **THEN** the service MUST NOT start periodic database-backed bridge ring registration or heartbeat tasks
- **AND** request-path bridge ownership lookups MUST fall back to the normalized static ring derived from settings
- **AND** HTTP bridge routing behavior for a single-instance deployment MUST remain deterministic

#### Scenario: Non-SQLite deployment keeps dynamic bridge ring membership
- **WHEN** the deployment uses a non-SQLite database
- **AND** the HTTP responses session bridge is enabled
- **THEN** the service MAY register and heartbeat bridge ring membership through the shared database
