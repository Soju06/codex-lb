## ADDED Requirements

### Requirement: WebSocket opening-handshake timeouts remain client-compatible and log distinctly

The system SHALL preserve the existing OpenAI-format
`upstream_unavailable` downstream error when an upstream Responses WebSocket
opening handshake times out before any downstream frame is emitted, and SHALL
record the persisted request-log error code as
`upstream_websocket_open_timeout`.

#### Scenario: upstream websocket open timeout is classified in request logs

- **WHEN** a Responses WebSocket request fails during the upstream opening
  handshake because the open operation times out
- **THEN** the downstream client receives HTTP 502 with
  `error.code = "upstream_unavailable"`
- **AND** the request log records
  `error_code = "upstream_websocket_open_timeout"`
- **AND** the request log records `transport = "websocket"`
