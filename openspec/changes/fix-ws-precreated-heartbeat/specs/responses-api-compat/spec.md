## ADDED Requirements

### Requirement: Codex WebSocket pre-created turns receive application heartbeats
When serving the Codex-native `/backend-api/codex/responses` WebSocket route, the proxy SHALL emit a parseable Codex vendor heartbeat while a `response.create` request is pending but upstream has not yet emitted `response.created`. The heartbeat MUST be an application text frame so Codex clients reset stream-idle watchdogs that do not observe WebSocket protocol ping/pong frames. Once upstream assigns a response id, the proxy MUST continue using the existing `response.in_progress` heartbeat shape for that response id.

#### Scenario: Codex websocket upstream is silent before response.created
- **GIVEN** a Codex-native WebSocket `/backend-api/codex/responses` request is pending
- **AND** upstream has not emitted `response.created` for the request
- **WHEN** no upstream application frame arrives before the configured keepalive interval
- **THEN** the proxy emits a `codex.keepalive` text event downstream
- **AND** the request remains pending for the upstream `response.created` or terminal event

#### Scenario: OpenAI-style v1 websocket does not receive Codex vendor heartbeat
- **GIVEN** an OpenAI-style WebSocket `/v1/responses` request is pending
- **AND** upstream has not emitted `response.created` for the request
- **WHEN** no upstream application frame arrives before the configured keepalive interval
- **THEN** the proxy MUST NOT emit a `codex.keepalive` vendor event downstream
