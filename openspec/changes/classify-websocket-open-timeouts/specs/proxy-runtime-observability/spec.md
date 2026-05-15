## ADDED Requirements

### Requirement: Websocket opening timeouts are distinguishable in request logs
The system SHALL persist a distinct request-log error code for upstream websocket opening-handshake timeouts while preserving the existing client-facing OpenAI-compatible websocket error contract.

#### Scenario: websocket open timeout is logged distinctly
- **WHEN** an upstream Responses websocket open attempt times out before the proxy has emitted downstream response frames
- **THEN** the persisted request_logs.error_code is upstream_websocket_open_timeout
- **AND** the downstream websocket error payload remains upstream_unavailable

#### Scenario: refresh timeout is not classified as websocket open timeout
- **WHEN** a token refresh or other pre-open transport step times out before the upstream websocket open attempt begins
- **THEN** the persisted request-log error code remains upstream_unavailable

### Requirement: Live snapshot exposes safe runtime correlation groups
The live snapshot helper SHALL expose bounded request-log correlation groups that let operators compare status, error code, model, transport, tier, token bucket, latency, and cost without exposing prompts, auth headers, or raw request payloads.

#### Scenario: snapshot includes aggregate correlation groups
- **WHEN** the live snapshot helper reads recent request logs
- **THEN** the output includes aggregate correlation groups by model, transport, requested tier, actual tier, status, error code, reasoning effort, and token buckets
- **AND** the output does not include raw prompt text, bearer tokens, auth headers, or full request payloads
