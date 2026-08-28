# responses-api-compat - Delta

## ADDED Requirements

### Requirement: Request-scoped Codex metadata survives HTTP-to-WebSocket bridging

When an HTTP Responses request is translated into an upstream WebSocket `response.create` frame, the service MUST project nonblank `x-codex-turn-metadata`, `x-openai-subagent`, `x-codex-parent-thread-id`, and `x-codex-window-id` compatibility headers into that frame's `client_metadata`. This projection MUST happen for every request, including requests multiplexed over a reused upstream socket. A metadata value already supplied in the request body MUST remain authoritative over the compatibility header, and header matching MUST be case-insensitive.

#### Scenario: Reused bridge session receives a subagent turn

- **GIVEN** a parent HTTP request has opened an upstream Responses WebSocket
- **WHEN** a subagent HTTP request reuses that socket with subagent, parent-thread, and child-window headers
- **THEN** the subagent request's `response.create.client_metadata` contains those values
- **AND** the earlier parent frame retains its own window metadata
- **AND** no value is inherited solely from the socket handshake

#### Scenario: Body metadata remains canonical

- **WHEN** a request body and compatibility header provide different values for the same Codex metadata key
- **THEN** the upstream `response.create.client_metadata` retains the body value

### Requirement: Compact routing honors turn-state affinity

When a compact request carries a nonblank `x-codex-turn-state`, the service MUST classify that value as Codex-session affinity before considering a session header, prompt-cache affinity, or sticky-thread affinity. This precedence MUST apply even when generic Codex session-header affinity is disabled, matching the normal Responses path.

#### Scenario: Turn-state-only compact remains on the turn owner

- **GIVEN** a Responses turn established an account mapping for an `x-codex-turn-state` value
- **AND** another account becomes preferable under the non-sticky routing strategy
- **WHEN** `/responses/compact` carries only that turn-state continuity value
- **THEN** the compact request is routed to the account that owns the turn-state mapping

#### Scenario: Turn-state overrides less-specific affinity

- **WHEN** a compact request carries turn-state, session-header, and prompt-cache keys
- **THEN** its affinity key is the turn-state value
- **AND** its affinity kind is Codex session

### Requirement: Namespaced side-effect replay dedupe preserves call identity

For a namespaced side-effect function or custom-tool call, the service MUST use the call's namespace and call ID as part of downstream and replayed-history deduplication identity. An exact replay with the same namespace, name, call ID, and canonical arguments MUST remain suppressed. Calls with different namespaces or different nonblank call IDs MUST remain distinct, even when their names and canonical arguments match, and their matching outputs MUST remain in forwarded history.

Flat legacy side-effect calls MAY continue to use argument-based replay identity so reconnects that change only a call ID do not repeat shell, patch, or terminal side effects.

#### Scenario: Distinct namespaced spawns use identical arguments

- **WHEN** two `collaboration.spawn_agent` calls have identical arguments and different call IDs
- **THEN** both calls are forwarded
- **AND** both matching outputs remain in replayed request history

#### Scenario: Exact namespaced call is replayed after reconnect

- **WHEN** reconnect replay emits the same namespaced call ID and canonical arguments under a new response ID
- **THEN** the service suppresses the replayed downstream call

#### Scenario: Equal call identity appears in different namespaces

- **WHEN** two side-effect calls share a name, call ID, and arguments but have different namespaces
- **THEN** the service treats them as distinct calls
