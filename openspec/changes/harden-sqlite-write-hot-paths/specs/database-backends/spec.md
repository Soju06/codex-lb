## MODIFIED Requirements

### Requirement: SQLite lock pressure is retried on write-hot paths

The service MUST handle transient SQLite lock errors with bounded retry/backoff
for write-hot SQLite operations that are part of request admission, request-log
observability, usage reservation, bridge ownership, sticky-session routing, or
liveness bookkeeping before returning a 5xx response or dropping a best-effort
write. For in-process SQLite serving, these write-hot paths MUST also serialize
local write attempts or use an equivalent single-writer mechanism so retry is
not the only mitigation for expected writer overlap.

#### Scenario: Usage reservation write hits a transient SQLite lock

- **WHEN** a usage reservation insert or update fails because SQLite reports the database is locked
- **THEN** the operation is retried with bounded backoff while the request budget remains available
- **AND** the final failure, if any, is logged with enough context to distinguish lock exhaustion from non-retryable database errors

#### Scenario: Bridge liveness write hits a transient SQLite lock

- **WHEN** a bridge or ring heartbeat write fails because SQLite reports the database is locked
- **THEN** the liveness write is retried with bounded backoff or deferred through the configured SQLite write-serialization path
- **AND** transient lock pressure does not immediately mark healthy bridge workers as failed

#### Scenario: Request-log write hits a transient SQLite lock

- **WHEN** inserting or rewriting a request-log row fails because SQLite reports the database is locked
- **THEN** the write is retried with bounded backoff after rolling back the failed transaction
- **AND** a transient lock does not immediately drop the request-log row

#### Scenario: Sticky-session write hits a transient SQLite lock

- **WHEN** upserting, deleting, or purging sticky-session rows fails because SQLite reports the database is locked
- **THEN** the write is retried with bounded backoff after rolling back the failed transaction
- **AND** a transient lock does not immediately lose routing affinity or cleanup progress
