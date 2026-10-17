## ADDED Requirements

### Requirement: Controlled origin probe covers every Responses transport

The traffic parity toolkit MUST provide an explicitly launched deterministic
origin fixture that supports Codex model discovery and Responses HTTP JSON,
HTTP SSE, and WebSocket requests on the canonical public and Codex-native path
forms. The fixture MUST accept multiple ordered `response.create` turns on one
WebSocket connection and MUST return a terminal lifecycle for every successful
accepted turn; the intentional `websocket_incomplete` failure scenario is
exempt. It MUST NOT perform upstream network calls or reflect request content or
credentials in responses. HTTP request bodies and WebSocket frames MUST be
bounded. The fixture launcher MUST bind to loopback by default and MUST reject
a non-loopback bind unless the operator supplies an explicit public-bind
acknowledgement.

#### Scenario: HTTP transports terminate deterministically

- **WHEN** the fixture receives valid `stream=false` and `stream=true`
  Responses requests
- **THEN** it returns HTTP JSON and SSE respectively
- **AND** both responses end in `response.completed` without copying request
  content into the response

#### Scenario: One WebSocket carries multiple turns

- **GIVEN** a client opens one fixture WebSocket
- **WHEN** it sends two valid `response.create` messages
- **THEN** the fixture emits two separate ordered created/completed lifecycles
- **AND** keeps the connection open between turns

#### Scenario: Accidental public launch is rejected

- **WHEN** the fixture launcher is given a non-loopback host without explicit
  public-bind acknowledgement
- **THEN** it exits before opening a listening socket

### Requirement: Controlled origin observations use the real socket boundary

The controlled-origin runbook MUST place a TLS/HTTP-capable reverse capture
boundary directly in front of the loopback fixture and configure the capture
addon with observer role `origin`. Path A and Path C MUST be captured in
separate files against the same observer id. Source equality from this setup
MAY be reported as controlled-origin public source evidence; forwarding headers
MUST NOT substitute for the capture boundary's socket peer. TLS certificate
trust, public listener exposure, and test-only credential handling MUST be
explicit operator responsibilities.

#### Scenario: Forwarded address cannot become origin evidence

- **GIVEN** a request supplies `Forwarded` or `X-Forwarded-For`
- **WHEN** the origin capture records source evidence
- **THEN** it derives the source from the capture process's client socket
- **AND** ignores the supplied forwarding headers for the source comparison
