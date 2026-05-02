## ADDED Requirements

### Requirement: Direct WebSocket previous-response misses do not expose raw upstream errors
When a direct Responses WebSocket follow-up receives an upstream `previous_response_not_found` error, the service MUST treat it as continuity loss. It MUST NOT expose the raw upstream `previous_response_not_found` error to the downstream client.

#### Scenario: short WebSocket continuation loses previous-response continuity
- **WHEN** a WebSocket `/v1/responses` or `/backend-api/codex/responses` follow-up includes `previous_response_id`
- **AND** upstream emits `previous_response_not_found` before assigning a response id
- **THEN** the service emits a retryable `stream_incomplete` failure for that request
- **AND** it does not replay the same stale `previous_response_id`
- **AND** it does not expose `previous_response_not_found` downstream
