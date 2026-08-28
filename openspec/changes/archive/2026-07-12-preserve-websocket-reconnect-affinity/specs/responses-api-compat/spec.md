## MODIFIED Requirements

### Requirement: Codex backend session_id preserves account affinity
When a backend Codex Responses or compact request includes a non-empty accepted session header, the service MUST use that value as the routing affinity key for upstream account selection unless the client supplied a non-empty `x-codex-turn-state` header. If the request lacks a client-supplied `prompt_cache_key`, the service MUST derive and attach a stable `prompt_cache_key` before upstream forwarding so account affinity and upstream prompt-cache routing can coexist. Accepted session headers are `session_id`, `session-id`, `x-codex-session-id`, `x-codex-conversation-id`, and `thread-id`, in that priority order.

A turn state synthesized by the proxy for the current downstream WebSocket handshake MUST NOT override a client-supplied session header or prompt-cache key for routing or WebSocket continuity selection. The proxy MUST seed WebSocket continuity storage under that synthesized turn state so a later client echo can reuse the completed-turn owner. The proxy MUST continue to forward that synthesized turn state upstream. A turn state sent by the client, including one that the proxy generated and the client later echoed, remains a client-supplied turn-state affinity key.

When a WebSocket handshake has neither a client-supplied turn state nor an accepted session header, the proxy MUST store its generated turn state as the WebSocket continuity key. A later connection that echoes that accepted value MUST recover the same continuity state.

#### Scenario: Backend Codex request derives prompt_cache_key before codex-session routing
- **WHEN** `/backend-api/codex/responses` is called with `session_id` and without `prompt_cache_key`
- **THEN** the routing decision still uses durable `codex_session` affinity for account selection
- **AND** the forwarded upstream payload includes a derived stable `prompt_cache_key`

#### Scenario: backend WebSocket reconnect retains session affinity despite a generated turn state
- **WHEN** two backend Codex Responses WebSocket connections include the same accepted session header and omit `x-codex-turn-state`
- **AND** the proxy generates a distinct turn state for each handshake
- **THEN** both account selections use the session header as the durable `codex_session` affinity key
- **AND** each generated turn state is still forwarded to the upstream

#### Scenario: echoed generated turn state remains a client continuation key
- **WHEN** a client reconnects with a non-empty `x-codex-turn-state` value it received from an earlier proxy handshake
- **THEN** that turn state remains the routing and WebSocket continuity key ahead of a broader accepted session header
- **AND** full-resend continuity for that echoed turn state can reuse the earlier completed response anchor

#### Scenario: generated turn state seeds continuity without a session header
- **WHEN** a backend Codex Responses WebSocket handshake omits both an accepted session header and `x-codex-turn-state`
- **AND** the proxy generates and returns a turn state for that handshake
- **THEN** the proxy stores its WebSocket continuity state under that generated value
- **AND WHEN** a later connection sends that value in `x-codex-turn-state`
- **THEN** it recovers the stored continuity state
