## ADDED Requirements

### Requirement: HTTP session bridge admission obeys downstream transport policy

Before an HTTP/SSE Responses request enters the upstream WebSocket session bridge, the proxy MUST apply the same explicit-transport precedence and effective `http_downstream_transport_policy` used by the ordinary streaming retry path. An explicit upstream `http` selection MUST bypass the bridge, an explicit upstream `websocket` selection MUST retain it, and otherwise the per-key override or global policy MUST decide. A bridge bypass MUST continue through the ordinary HTTP streaming path without changing request or response shapes.

#### Scenario: Always-HTTP bypasses an enabled bridge

- **GIVEN** the HTTP Responses session bridge is enabled
- **AND** the effective downstream-HTTP policy is `always_http` or `pinned`
- **WHEN** a downstream HTTP/SSE request is handled
- **THEN** the request bypasses the WebSocket session bridge
- **AND** is sent through the ordinary upstream HTTP path

#### Scenario: Smart bridge admission follows continuity signals

- **GIVEN** the HTTP Responses session bridge is enabled
- **AND** the effective policy is `smart`
- **WHEN** a request has no sticky-continuation signal
- **THEN** it bypasses the bridge
- **BUT WHEN** any defined sticky-continuation signal is present
- **THEN** it remains eligible for the bridge

#### Scenario: Explicit transport wins before bridge admission

- **GIVEN** the HTTP Responses session bridge is enabled
- **WHEN** upstream transport is explicitly `http`
- **THEN** the bridge is bypassed under every policy
- **BUT WHEN** upstream transport is explicitly `websocket`
- **THEN** the bridge remains enabled under every policy

#### Scenario: Per-key policy controls bridge admission

- **GIVEN** a per-API-key transport policy override is non-null
- **WHEN** bridge admission is evaluated
- **THEN** that override is used instead of the global policy
