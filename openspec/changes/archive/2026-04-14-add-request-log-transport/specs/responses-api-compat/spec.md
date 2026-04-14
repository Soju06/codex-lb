## ADDED Requirements

### Requirement: Persist request log transport for Responses requests
The service MUST persist a stable `transport` value on `request_logs` for Responses proxy requests and MUST expose the same value through `/api/request-logs`. Requests accepted over HTTP on `/backend-api/codex/responses` or `/v1/responses` MUST persist `transport = "http"`. Requests accepted over WebSocket on those paths MUST persist `transport = "websocket"`.

#### Scenario: HTTP Responses request logs http transport
- **WHEN** a client completes a Responses request over HTTP on `/backend-api/codex/responses` or `/v1/responses`
- **THEN** the persisted request log has `transport = "http"`
- **AND** `/api/request-logs` returns that row with `transport = "http"`

#### Scenario: WebSocket Responses request logs websocket transport
- **WHEN** a client completes a Responses request over WebSocket on `/backend-api/codex/responses` or `/v1/responses`
- **THEN** the persisted request log has `transport = "websocket"`
- **AND** `/api/request-logs` returns that row with `transport = "websocket"`
