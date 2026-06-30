## MODIFIED Requirements

### Requirement: Local overload reasons are stable and distinguishable

Local Responses overload failures MUST expose stable low-cardinality reason
fields in logs and metrics so operators can distinguish `bridge_queue_full`,
`response_create_gate_timeout`, `hard_affinity_saturated`,
`previous_response_owner_unavailable`, `global_admission_timeout`,
`capacity_exhausted_active_sessions`, `account_response_create_cap`, and
`account_stream_cap`. These local reasons MUST NOT be reported as upstream rate
limits.

#### Scenario: Queued bridge requests wait for the response-create gate within timeout

- **WHEN** a visible HTTP bridge request has already claimed a bridge queue slot
- **AND** the per-session `response_create_gate` is held by legitimate in-flight work
- **THEN** the request waits for the gate until the configured `proxy_admission_wait_timeout_seconds` elapses
- **AND** if the timeout elapses first, the request is rejected with `response_create_gate_timeout`
- **AND** `bridge_queue_full` remains the bounded local-overload reason when the bridge queue itself is saturated
