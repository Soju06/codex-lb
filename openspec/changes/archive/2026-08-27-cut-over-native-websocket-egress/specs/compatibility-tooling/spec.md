## MODIFIED Requirements

### Requirement: Upstream WebSocket handshake offers Codex-compatible compression

Direct upstream WebSockets MUST use the Codex-pinned OpenAI `tokio-tungstenite` and `tungstenite` revisions when the fixed helper is available before dispatch and MUST enable their default `permessage-deflate` configuration. Account-routed upstream WebSocket clients MUST continue offering `permessage-deflate` through their route-aware library. A route choice MUST NOT remove the extension family, and traffic reports MUST continue comparing negotiated extensions independently from TLS and header identity.

#### Scenario: Direct and routed WebSocket handshakes offer compression

- **WHEN** the proxy opens an upstream Responses WebSocket directly or through an account route
- **THEN** its handshake offers `permessage-deflate`
- **AND** the chosen route does not change the extension family

#### Scenario: Native direct handshake offers Codex compression

- **GIVEN** the fixed native helper is available
- **WHEN** a direct upstream WebSocket handshake is sent
- **THEN** it is serialized by the Codex-pinned tungstenite implementation
- **AND** it offers that implementation's default `permessage-deflate` parameters

#### Scenario: Routed handshake retains compression

- **WHEN** an account-routed upstream WebSocket handshake is sent
- **THEN** the route-aware client still offers `permessage-deflate`
- **AND** the traffic report does not collapse compression parity into TLS parity
