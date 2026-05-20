## ADDED Requirements

### Requirement: HTTP bridge startup wait timeouts are logged

When an HTTP bridge startup wait times out locally, the service MUST log the request id, timeout stage, timeout seconds, and low-cardinality bridge affinity family. The log MUST NOT include raw prompt-cache keys, session ids, turn-state ids, API keys, or request payload content.

#### Scenario: Bridge startup admission timeout is diagnosable

- **WHEN** a HTTP bridge startup wait exceeds the configured proxy admission wait timeout
- **THEN** the console log includes the timeout stage and request id
- **AND** the log includes only low-cardinality affinity metadata, not raw affinity key values
