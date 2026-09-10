## MODIFIED Requirements

### Requirement: Responses WebSocket preserves bidirectional transport semantics

The Responses WebSocket relay MUST preserve ordered text and binary messages, selected subprotocol response metadata, close codes, and terminal error delivery across its downstream and upstream boundaries. Direct and account-routed upstream connections MUST use native Codex-family WebSocket egress when the fixed helper is available before dispatch, while Python MUST retain route-aware endpoint selection, fallback safety, metadata, and cleanup. Ping and pong control frames MUST remain transport-owned and MUST NOT surface as application events. A frame whose native send acknowledgement is ambiguous or failed MUST NOT be replayed.

#### Scenario: Native direct relay preserves frames

- **GIVEN** a direct or account-routed Responses WebSocket uses the native helper
- **WHEN** text and binary frames travel in both directions
- **THEN** their type, payload, and ordering are preserved
- **AND** control ping and pong frames are handled below the application relay

#### Scenario: Native direct relay preserves terminal close

- **WHEN** the native upstream sends a close frame
- **THEN** the relay observes its close code and reason
- **AND** the native connection is removed from the helper's active registry

#### Scenario: Ambiguous native frame send fails closed

- **GIVEN** a downstream `response.create` frame is dispatched to the helper
- **WHEN** acknowledgement fails because the helper or connection closes
- **THEN** the turn surfaces a terminal transport failure
- **AND** the frame is not resent on another transport
