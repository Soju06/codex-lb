## ADDED Requirements

### Requirement: Docker guidance includes a SQLite-conservative profile

Docker installation guidance MUST include a SQLite-conservative runtime profile for operators who intentionally stay on SQLite. This profile MUST be distinct from the PostgreSQL higher-concurrency profile.

#### Scenario: SQLite-conservative Docker profile limits request workers

- **WHEN** an operator follows the documented SQLite-conservative Docker profile
- **THEN** the example keeps the SQLite database URL
- **AND** the example configures a single request worker or an equivalent write-serialized runtime
- **AND** the guidance explains that this profile trades throughput for fewer SQLite writer-lock failures

#### Scenario: Higher-concurrency Docker guidance remains PostgreSQL-backed

- **WHEN** an operator needs sustained multi-worker throughput
- **THEN** Docker guidance points to the PostgreSQL-backed profile rather than recommending unconstrained multi-worker SQLite
- **AND** the standard listener and OAuth callback ports remain unchanged
