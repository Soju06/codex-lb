## ADDED Requirements

### Requirement: SQLite live profile controls write contention

When `codex-lb` is configured to use SQLite for live serving, official behavior and guidance MUST treat SQLite as a single-writer backend and MUST provide a supported operating profile that limits concurrent write contention without requiring PostgreSQL.

#### Scenario: SQLite live profile is conservative by default

- **WHEN** an operator chooses SQLite for live Docker or long-running local serving
- **THEN** the supported SQLite profile limits runtime write concurrency through a single request worker, a single writer, or an equivalent serialization mechanism
- **AND** the profile preserves the existing SQLite database URL format

#### Scenario: Multi-worker SQLite requires explicit write hardening

- **WHEN** SQLite is used with more than one request worker
- **THEN** write-hot paths serialize writes or apply bounded lock retry/backoff before returning a server error
- **AND** the runtime does not rely on SQLite busy timeout alone as the only mitigation for expected concurrent writer overlap

### Requirement: SQLite lock pressure is retried on write-hot paths

Write-hot SQLite operations that are part of request admission, usage reservation, bridge ownership, or liveness bookkeeping MUST handle transient SQLite lock errors with bounded retry/backoff before returning a 5xx response.

#### Scenario: Usage reservation write hits a transient SQLite lock

- **WHEN** a usage reservation insert or update fails because SQLite reports the database is locked
- **THEN** the operation is retried with bounded backoff while the request budget remains available
- **AND** the final failure, if any, is logged with enough context to distinguish lock exhaustion from non-retryable database errors

#### Scenario: Bridge liveness write hits a transient SQLite lock

- **WHEN** a bridge or ring heartbeat write fails because SQLite reports the database is locked
- **THEN** the liveness write is retried with bounded backoff or deferred through the configured SQLite write-serialization path
- **AND** transient lock pressure does not immediately mark healthy bridge workers as failed
