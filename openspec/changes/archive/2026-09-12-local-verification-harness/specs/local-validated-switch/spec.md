## ADDED Requirements

### Requirement: Reproducible local verification
The local verification harness SHALL gate inference on successful candidate readiness, use bounded stage deadlines, emit progress while waiting and preserve completed stage evidence on failure. It SHALL reject failed, incomplete, malformed or unterminated Responses streams. An optional compaction check SHALL recover a fresh marker from compact state without repeating it in the continuation, including across explicitly selected replicas. Reports SHALL omit raw responses, encrypted state and credentials. The harness SHALL NOT restart processes or change client routing.

#### Scenario: Candidate is not ready
- **WHEN** readiness fails or its deadline expires
- **THEN** no inference request is sent and the report identifies the failed readiness stage

#### Scenario: Slow or invalid inference
- **WHEN** an inference emits heartbeats without completing, malformed frames, errors or an incomplete terminal
- **THEN** the bounded stage fails, closes its owned connection and retains earlier successful stages

#### Scenario: Isolated release test
- **WHEN** release tests are requested with a fixture Git reference
- **THEN** the harness resolves that reference before writing fixtures, tests the selected release in an isolated test directory and temporary SQLite database, verifies the actual application import path, records fixture provenance, and cleans up owned child processes on timeout or cancellation

#### Scenario: Idle telemetry is incomplete
- **WHEN** read-only idle verification sees active requests, active entrance routing, bridge errors or ambiguous missing fields
- **THEN** it fails with a named reason; only an exact cold-idle telemetry shape with zero entrance connections may omit bridge activity
