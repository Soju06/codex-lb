## ADDED Requirements

### Requirement: Request logs persist upstream elapsed timing separately from proxy latency
The proxy MUST persist a nullable `elapsed_ms` timing on `request_logs` that measures time from immediately before upstream submission until upstream completion for that logged request. The existing `latency_ms` field MUST remain a separate proxy-wide latency measurement and MUST NOT be reinterpreted as `elapsed_ms`.

#### Scenario: Unary request records distinct elapsed and latency timings
- **WHEN** a unary proxy request spends time in local selection or routing before the upstream request is submitted
- **AND** the request later completes successfully
- **THEN** the persisted `request_logs` row includes a non-null `elapsed_ms` measured from just before upstream submission until upstream completion
- **AND** the persisted row keeps `latency_ms` as the broader proxy latency measurement

#### Scenario: Streaming request records elapsed timing through the final SSE frame
- **WHEN** an HTTP/SSE proxy request starts an upstream stream successfully
- **AND** the request log row is written after the final emitted SSE frame or terminal stream event
- **THEN** the persisted `request_logs` row includes a non-null `elapsed_ms` covering that upstream stream lifetime

#### Scenario: Pre-upstream failure leaves elapsed timing unset
- **WHEN** a request fails before the proxy submits any upstream request
- **THEN** the persisted `request_logs` row stores `elapsed_ms = null`
- **AND** the row may still store `latency_ms` when the proxy measured local handling time
