# responses-api-compat delta

## MODIFIED Requirements

### Requirement: Request logs expose upstream Responses transport
For streaming Responses proxy requests, persisted request logs MUST distinguish the downstream client transport from the upstream egress transport by recording the upstream transport in `request_logs.upstream_transport` while preserving `request_logs.transport` as the downstream client transport.

The recorded `upstream_transport` MUST be the concrete transport the proxy resolved for the upstream attempt, `"http"` or `"websocket"`. It MUST NOT be the configured `upstream_stream_transport` strategy value `"auto"`. This applies to every raw (non-bridge) streaming path, including a request that bypassed the HTTP responses bridge for any reason. Recording the resolved transport MUST NOT change the transport mode handed to the upstream client: a base `"auto"` mode kept by the downstream-HTTP policy MUST still reach the upstream client as `"auto"`, so that the WebSocket-handshake rejection fallback to upstream HTTP stays available.

The `upstream_transport` label of `codex_lb_upstream_transport_decisions_total` for a request MUST equal the value persisted in that request's log row.

#### Scenario: downstream HTTP single-shot records upstream HTTP
- **GIVEN** the downstream request transport is HTTP
- **AND** smart HTTP-downstream routing chooses upstream HTTP for a single-shot Responses request
- **WHEN** the request log is persisted
- **THEN** `transport` is `"http"`
- **AND** `upstream_transport` is `"http"`

#### Scenario: downstream HTTP sticky records preserved auto upstream mode
- **GIVEN** the downstream request transport is HTTP and `upstream_stream_transport` is `"auto"`
- **AND** smart HTTP-downstream routing keeps the base upstream `"auto"` mode for a sticky Responses request served on the raw (non-bridge) path, and that mode resolves to upstream WebSocket
- **WHEN** the request log is persisted
- **THEN** `transport` is `"http"`
- **AND** `upstream_transport` is `"websocket"`, not `"auto"`
- **AND** the upstream client still received transport mode `"auto"`

#### Scenario: bridge-bypassed request records the resolved transport, not auto
- **GIVEN** the HTTP responses bridge is enabled, `upstream_stream_transport` is `"auto"`, and the effective policy is `always_websocket`
- **AND** the model prefers upstream WebSocket
- **WHEN** a request that bypasses the bridge with reason `image` completes on the raw (non-bridge) path
- **THEN** the persisted `upstream_transport` is `"websocket"`
- **AND** it is not `"auto"`
- **AND** the transport decision counter records `upstream_transport="websocket"` for that request

#### Scenario: bypassed request pinned to HTTP records HTTP
- **GIVEN** a request bypasses the bridge and the ordinary precedence resolves upstream HTTP (oversized payload, external image URL, `image_generation`, or a recent upstream WebSocket failure)
- **WHEN** the request log is persisted
- **THEN** `upstream_transport` is `"http"`

#### Scenario: historical or unrelated rows tolerate missing upstream transport
- **GIVEN** a request log row predates upstream transport persistence or belongs to a request kind that does not know its upstream transport
- **WHEN** the row is read
- **THEN** `upstream_transport` MAY be null
- **AND** the existing request-log response MUST remain valid
