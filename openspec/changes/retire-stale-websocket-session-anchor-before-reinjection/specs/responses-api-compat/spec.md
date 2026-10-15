# responses-api-compat Delta

## ADDED Requirements

### Requirement: Direct WebSocket session anchors remembered as denied are retired instead of re-injected

When a direct Responses WebSocket `response.create` carries no client `previous_response_id`, Codex session affinity is enabled, and session continuity holds a completed-response anchor (`last_completed_response_id`) that the process-local stale previous-response memory records as denied for the request's API key id, the service MUST NOT inject that anchor. Before the stored-prefix comparison it MUST retire the anchor from session continuity — clearing the completed response id, the stored input count, the stored input prefix fingerprint, and the pending tool-call metadata that exist only for that anchor — so the same id cannot be injected again once the stale memory entry expires. The service MUST log `websocket_session_anchor_retired` with `reason=stale_previous_response` for the retired anchor. The request MUST then be dispatched with the client's own untrimmed `input`, without `previous_response_id`, and its request state MUST NOT be marked as carrying a proxy-injected anchor. The stale memory MUST be consulted with the same API key id the fail-closed path used when it remembered the denial. This rule MUST NOT alter a client-supplied `previous_response_id`, and a continuity anchor that is not remembered as denied MUST keep the existing injection behavior.

#### Scenario: Full-context retry after a denied injected anchor goes upstream unanchored

- **GIVEN** a direct Responses WebSocket session whose continuity state holds `last_completed_response_id = resp_denied_anchor` with a matching stored prefix and pending tool-call metadata
- **AND** an earlier turn on that anchor failed closed with `previous_response_not_found` without a retry-safe fresh replay, so `resp_denied_anchor` is remembered as stale for the request's API key
- **WHEN** the client re-sends its full context as a `response.create` without `previous_response_id`
- **THEN** the upstream payload carries no `previous_response_id`
- **AND** the upstream `input` is the client's full untrimmed input
- **AND** the request state is not marked as carrying a proxy-injected anchor
- **AND** continuity no longer holds `resp_denied_anchor`, its stored input count, its prefix fingerprint, or its pending tool-call metadata
- **AND** the service logs `websocket_session_anchor_retired … reason=stale_previous_response`

#### Scenario: Anchor not remembered as denied is still injected

- **GIVEN** a direct Responses WebSocket session whose continuity anchor is not remembered as stale for the request's API key
- **WHEN** the client sends a follow-up whose input prefix matches the stored context
- **THEN** the service injects `previous_response_id = last_completed_response_id` and trims the stored prefix exactly as before

#### Scenario: Client-supplied anchor is never retired by this rule

- **GIVEN** a direct Responses WebSocket `response.create` that carries its own `previous_response_id`
- **WHEN** the session-anchor decision runs
- **THEN** no session anchor is injected or retired
- **AND** the client's `previous_response_id` is forwarded unchanged
