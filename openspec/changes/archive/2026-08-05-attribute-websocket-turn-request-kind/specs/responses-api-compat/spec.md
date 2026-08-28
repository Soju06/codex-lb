## MODIFIED Requirements

### Requirement: Codex WebSocket prewarm completions are classified separately
For a direct Responses WebSocket, the service MUST treat Codex turn metadata received on the HTTP handshake as connection-scoped metadata rather than applying its `request_kind` to every `response.create` frame. The service MUST classify an individual turn as `prewarm` when the connection metadata is `prewarm` and either that turn carries `generate: false` or its completed usage reports zero output tokens. Other turns on the same connection MUST be classified as `normal`.

Request logs for direct Responses WebSocket turns MUST persist the connection-scoped value separately as `connection_request_kind`. Empty-output prewarm completions MUST NOT update account success state or previous-response ownership, while still allowing the upstream terminal frame to pass through.

#### Scenario: generated turn on a prewarm-opened connection is normal
- **GIVEN** a direct Responses WebSocket handshake carries `x-codex-turn-metadata` with `request_kind: "prewarm"`
- **WHEN** a later `response.create` does not carry `generate: false` and upstream completes it with non-zero output tokens
- **THEN** the request log records `request_kind` as `normal`
- **AND** the request log records `connection_request_kind` as `prewarm`
- **AND** the completion remains eligible to update account success state and previous-response ownership

#### Scenario: empty prewarm completion does not look like user turn progress
- **GIVEN** a direct Responses WebSocket handshake carries `x-codex-turn-metadata` with `request_kind: "prewarm"`
- **WHEN** a `response.create` carries `generate: false` or upstream completes it with zero output tokens
- **THEN** the request log records `request_kind` as `prewarm`
- **AND** the request log records `connection_request_kind` as `prewarm`
- **AND** the service does not mark the account successful for that completion
- **AND** the service does not remember the response id as a usable previous-response owner

#### Scenario: failed generated turn on a prewarm-opened connection is normal
- **GIVEN** a direct Responses WebSocket handshake carries `x-codex-turn-metadata` with `request_kind: "prewarm"`
- **AND** a later `response.create` does not carry `generate: false`
- **WHEN** that turn fails before completed usage is available
- **THEN** the request log records `request_kind` as `normal`
- **AND** the request log records `connection_request_kind` as `prewarm`
