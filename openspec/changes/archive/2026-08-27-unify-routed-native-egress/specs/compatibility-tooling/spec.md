## MODIFIED Requirements

### Requirement: Upstream WebSocket handshake offers Codex-compatible compression

Direct and account-routed upstream WebSockets MUST use the Codex-pinned OpenAI `tokio-tungstenite` and `tungstenite` revisions when the fixed helper is available before dispatch and MUST enable their default `permessage-deflate` configuration. When the helper is unavailable before dispatch, account-routed and direct Python WebSocket clients MUST continue offering `permessage-deflate`. A route choice MUST NOT remove the extension family or change the preferred implementation family when the helper is present, and traffic reports MUST continue comparing negotiated extensions independently from TLS and header identity.

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
- **THEN** an available helper uses the same Codex-pinned native compression configuration as direct egress
- **AND** a pre-dispatch missing-helper fallback still offers `permessage-deflate`
- **AND** the traffic report does not collapse compression parity into TLS parity
