## ADDED Requirements

### Requirement: Server-observable parity keeps identity and transport dimensions separate

The traffic parity toolkit MUST compare direct Codex and codex-lb observations separately for HTTP protocol/ALPN, TLS handshake profile, HTTP identity headers, SSE framing, and WebSocket handshake/extension behavior. It MUST NOT report header parity as TLS or full traffic indistinguishability, and SHOULD identify dimensions that cannot be controlled without a shared egress origin.

#### Scenario: Header alignment does not conceal TLS mismatch

- **GIVEN** direct and proxied requests have matching normalized Codex identity headers
- **AND** their ALPN, cipher list, or TLS extension profile differs
- **WHEN** a parity report is generated
- **THEN** identity parity is reported independently
- **AND** TLS/HTTP transport remains a visible mismatch

### Requirement: Model discovery emits a Codex control identity

Subscription model-discovery requests MUST send the resolved Codex client version in a first-party Codex `User-Agent`, `originator`, and `version` header family, MUST use `Accept: */*`, and MUST apply the same mapping for direct and account-routed egress. They MUST NOT expose the HTTP library's default User-Agent as the request identity.

#### Scenario: Routed and direct model discovery share identity

- **GIVEN** the same resolved Codex version, access token, and account
- **WHEN** model discovery runs once through direct egress and once through an account route
- **THEN** both requests carry the same Codex identity and accept headers
- **AND** neither carries an aiohttp-generated User-Agent

### Requirement: Upstream WebSocket handshake offers Codex-compatible compression

The direct and account-routed upstream WebSocket clients MUST offer `permessage-deflate` using their library's standard maximum-window negotiation, matching the extension family offered by Codex. A route choice MUST NOT remove that extension offer.

#### Scenario: Direct and routed WebSocket handshakes offer compression

- **WHEN** the proxy opens an upstream Responses WebSocket directly or through an account route
- **THEN** its handshake offers `permessage-deflate`
- **AND** the chosen route does not change the extension family
