## ADDED Requirements

### Requirement: HTTP bridge startup wait timeouts are logged

When an HTTP bridge startup wait times out locally, the service MUST log the request id, timeout stage, timeout seconds, and low-cardinality bridge affinity family. The log MUST NOT include raw prompt-cache keys, session ids, turn-state ids, API keys, or request payload content.

#### Scenario: Bridge startup admission timeout is diagnosable

- **WHEN** a HTTP bridge startup wait exceeds the configured proxy admission wait timeout
- **THEN** the console log includes the timeout stage and request id
- **AND** the log includes only low-cardinality affinity metadata, not raw affinity key values

#### Scenario: Response-create gate timeout identifies stale pending work

- **WHEN** a HTTP bridge request times out waiting for the per-session response-create gate
- **THEN** the console log includes the hashed bridge key, pending count, queued count, gate availability, and pending request ids with ages
- **AND** the log does not include raw affinity key values or request payload content

### Requirement: Stale pending HTTP bridge retirement is logged

When the service retires an HTTP bridge session because pending precreated replay cannot make progress after upstream close or timeout, the service MUST emit a `retire_stale_pending` bridge event with low-cardinality bridge metadata and the terminal detail code.

#### Scenario: Failed precreated replay emits retirement event

- **WHEN** precreated HTTP bridge replay fails after upstream close or timeout
- **THEN** the console log includes a HTTP bridge event with `event=retire_stale_pending`
- **AND** the event includes only hashed bridge identity and low-cardinality metadata
