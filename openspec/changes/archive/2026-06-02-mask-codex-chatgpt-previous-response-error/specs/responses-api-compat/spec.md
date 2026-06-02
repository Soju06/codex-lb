## ADDED Requirements

### Requirement: Codex WebSocket top-level previous-response errors are masked
When serving the Codex-native `/backend-api/codex/responses` WebSocket route, the proxy MUST treat upstream `type: "error"` frames with top-level error fields as upstream error envelopes if the frame does not contain a nested `error` object. If those fields describe a `previous_response_not_found` continuity miss, the proxy MUST use the existing continuity fail-closed behavior and MUST NOT forward raw `previous_response_not_found` or the missing response id to the downstream Codex client.

#### Scenario: ChatGPT backend emits top-level previous-response miss on Codex websocket
- **WHEN** a `/backend-api/codex/responses` WebSocket follow-up has `previous_response_id`
- **AND** the ChatGPT backend emits `{"type":"error","code":"previous_response_not_found","param":"previous_response_id",...}` without a nested `error` object
- **THEN** the downstream event is a retryable continuity failure such as `stream_incomplete`
- **AND** the downstream payload does not contain `previous_response_not_found`
- **AND** the downstream payload does not expose the missing previous response id
