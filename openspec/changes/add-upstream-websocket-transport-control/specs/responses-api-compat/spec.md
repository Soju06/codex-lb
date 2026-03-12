## ADDED Requirements
### Requirement: Streaming Responses transport is operator-selectable
For streaming Codex/Responses proxy requests, the system MUST let operators choose the upstream transport strategy through dashboard settings. The resolved strategy MAY be `auto`, `http`, or `websocket`, and `default` MUST defer to the server configuration default.

#### Scenario: Dashboard forces websocket upstream transport
- **WHEN** the dashboard setting `upstream_stream_transport` is set to `"websocket"`
- **THEN** streaming upstream `/backend-api/codex/responses` traffic MUST use the native Responses WebSocket transport
- **AND** the proxy MUST continue bridging the upstream stream back through the existing client-facing Responses interface

#### Scenario: Dashboard forces HTTP upstream transport
- **WHEN** the dashboard setting `upstream_stream_transport` is set to `"http"`
- **THEN** streaming upstream `/backend-api/codex/responses` traffic MUST use the existing HTTP Responses transport

#### Scenario: Auto transport falls back when websocket upgrades are rejected
- **WHEN** the resolved upstream transport strategy is `"auto"`
- **AND** the proxy initially selects the native Responses WebSocket transport
- **AND** the upstream rejects the websocket upgrade with HTTP `403`, `404`, or `426`
- **THEN** the proxy MUST retry the same streaming request over the existing HTTP Responses transport before failing the client stream

### Requirement: Fast service tier aliases priority upstream
When a Responses request includes `service_tier: "fast"`, the service MUST preserve the requested tier for local observability while normalizing the outbound upstream payload to `service_tier: "priority"`.

#### Scenario: Fast mode request remains locally visible
- **WHEN** a client sends a valid Responses request with `service_tier: "fast"`
- **THEN** the proxy accepts the request
- **AND** the outbound upstream request uses `service_tier: "priority"`
- **AND** the persisted request log keeps the client-requested tier visible as `"fast"`

### Requirement: Streaming request logs preserve requested service tier
When a streaming Responses request completes, the persisted request log MUST prefer the client-requested `service_tier` over any upstream-reported effective tier for the request-log `service_tier` field.

#### Scenario: Upstream downgrades the reported tier
- **WHEN** a client sends `service_tier: "priority"` for a streaming Responses request
- **AND** the upstream response later reports `service_tier: "auto"` or `"default"`
- **THEN** the persisted request log entry records `service_tier: "priority"`
