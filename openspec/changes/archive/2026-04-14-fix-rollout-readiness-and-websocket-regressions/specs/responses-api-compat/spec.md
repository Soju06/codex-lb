## ADDED Requirements

### Requirement: Bridge-enabled readiness fails closed on bridge metadata errors

When HTTP responses session bridging is enabled, readiness MUST fail closed if bridge ring metadata cannot be read or if the replica is not an active member of a non-empty ring. Readiness MAY stay healthy only while the ring is still empty and the replica is waiting for initial registration.

#### Scenario: Bridge lookup errors fail readiness

- **GIVEN** `http_responses_session_bridge_enabled=true`
- **AND** bridge ring metadata lookup returns an error
- **WHEN** `/health/ready` is evaluated
- **THEN** the service returns `503`

#### Scenario: Empty bridge ring does not block initial readiness

- **GIVEN** `http_responses_session_bridge_enabled=true`
- **AND** the bridge ring is empty
- **WHEN** `/health/ready` is evaluated
- **THEN** the service may still return success while the replica is registering

### Requirement: Websocket upstream health tracks the full request lifecycle

When websocket transport is selected for upstream Responses requests, the account circuit breaker MUST evaluate the full websocket request lifecycle rather than the handshake alone. A successful `101 Switching Protocols` handshake MUST NOT count as success until the upstream websocket produces a terminal response event, and the breaker MUST record a failure when the websocket errors or closes before a terminal event.

#### Scenario: Post-handshake websocket failure counts against the circuit breaker

- **WHEN** the upstream websocket handshake succeeds with `101`
- **AND** the websocket closes or errors before emitting a terminal response event
- **THEN** the account circuit breaker records a failure for that request lifecycle

#### Scenario: Completed websocket response counts as circuit-breaker success

- **WHEN** the upstream websocket handshake succeeds with `101`
- **AND** the websocket emits a terminal response event
- **THEN** the account circuit breaker records success for that request lifecycle

### Requirement: HTTP shutdown drain excludes websocket connection lifetimes

Graceful shutdown drain accounting for the HTTP service MUST wait only on in-flight HTTP request lifetimes. Long-lived websocket connections MUST NOT keep the HTTP drain counter above zero until the global timeout expires.

#### Scenario: Active websocket does not block HTTP drain

- **WHEN** graceful shutdown begins while websocket connections are still open
- **THEN** the HTTP drain wait ignores those websocket lifetimes
- **AND** shutdown proceeds without waiting for the global drain timeout solely because of those websocket sessions
