## MODIFIED Requirements

### Requirement: Codex installation metadata must be account-owned

Codex `response.create` requests sent through account-scoped HTTP/SSE, bridge,
or WebSocket transports MUST use the selected local account's stored
`x-codex-installation-id` value in `client_metadata`. For the observed Codex CLI
0.150.1 Responses wire profile, the same value MUST NOT be synthesized as a
standalone upstream HTTP request or WebSocket handshake header. Header location
is part of the wire profile and MUST be selected per transport rather than
inferred from the existence of an account installation id.

#### Scenario: Client-supplied installation id is replaced

- **GIVEN** a client sends `client_metadata.x-codex-installation-id`
- **AND** codex-lb selects account `A`
- **WHEN** codex-lb sends the upstream `response.create` request
- **THEN** the upstream `client_metadata.x-codex-installation-id` MUST equal account `A`'s stored installation id
- **AND** it MUST NOT equal the client-supplied value.

#### Scenario: Profiled Responses egress omits standalone installation header

- **GIVEN** codex-lb selects an account with a stored installation id
- **WHEN** it opens a Codex CLI 0.150.1-profiled Responses HTTP/SSE request or WebSocket connection
- **THEN** the selected account installation id MUST be present in each `response.create.client_metadata`
- **AND** `x-codex-installation-id` MUST be absent from the standalone upstream request or handshake headers.
