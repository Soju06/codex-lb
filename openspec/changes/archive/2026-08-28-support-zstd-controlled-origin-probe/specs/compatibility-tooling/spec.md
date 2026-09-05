## MODIFIED Requirements

### Requirement: Controlled origin probe covers every Responses transport

The traffic parity toolkit MUST provide an explicitly launched deterministic
origin fixture that supports Codex model discovery and Responses HTTP JSON,
HTTP SSE, and WebSocket requests on the canonical public and Codex-native path
forms. The fixture MUST accept multiple ordered `response.create` turns on one
WebSocket connection, MUST return a terminal lifecycle for every accepted
turn, and MUST NOT perform upstream network calls or reflect request content or
credentials in responses. The fixture MUST accept Codex HTTP requests encoded
with zstd, decode them before JSON parsing, and enforce its request-size bound
independently on the encoded and decoded bodies. Malformed zstd and unsupported
content encodings MUST be rejected as client errors. HTTP request bodies and
WebSocket frames MUST be bounded. The fixture launcher MUST bind to loopback by
default and MUST reject a non-loopback bind unless the operator supplies an
explicit public-bind acknowledgement.

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

#### Scenario: Real Codex zstd SSE request is accepted

- **GIVEN** a valid Responses JSON object compressed with zstd
- **WHEN** Codex sends it with `Content-Encoding: zstd`
- **THEN** the fixture decodes and parses the object
- **AND** returns the deterministic response in the requested HTTP mode

#### Scenario: Compressed request cannot bypass the fixture bound

- **GIVEN** a zstd request whose encoded body is within the request limit
- **AND** whose decoded body exceeds the request limit
- **WHEN** the fixture decodes the request
- **THEN** it rejects the request with payload-too-large status

#### Scenario: Invalid content encoding is rejected

- **WHEN** a request declares malformed zstd or an unsupported content encoding
- **THEN** the fixture rejects it as a client error
- **AND** does not interpret the encoded bytes as JSON
