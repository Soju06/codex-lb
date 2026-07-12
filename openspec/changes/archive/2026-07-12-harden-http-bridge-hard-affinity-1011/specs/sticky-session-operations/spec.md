## ADDED Requirements

### Requirement: Hard HTTP bridge reconnects remain account-bound after upstream close

When an HTTP responses bridge session uses a hard continuity key such as `turn_state_header` or `session_header`, replay or reconnect handling MUST NOT route the same pending request to a different upstream account solely because the prior upstream WebSocket closed with code `1011`.

Soft-affinity bridge sessions MAY continue to exclude the failed account for transient upstream close recovery when no hard continuity dependency is present.

#### Scenario: session-header bridge replay preserves owner account after 1011

- **GIVEN** an HTTP bridge session is keyed by `session_header`
- **AND** its upstream WebSocket closes with code `1011` before `response.completed`
- **WHEN** the bridge attempts a pre-created replay or reconnect for the pending request
- **THEN** the account selector is called with the current session account as the preferred account
- **AND** the current session account is not excluded solely because of the `1011` close
- **AND** the request is not replayed on another account unless an explicit non-1011 account-failure path requires it

### Requirement: HTTP bridge upstream WebSocket connects use WebSocket-safe headers

When HTTP responses bridge code opens or reconnects an upstream responses WebSocket, it MUST remove HTTP-only and hop-by-hop inbound headers before passing headers to the upstream WebSocket connector.

The upstream responses WebSocket header builder MUST NOT forward HTTP Responses API beta tokens such as `responses=experimental`; it MUST send the responses WebSocket beta token required by the upstream WebSocket protocol.

The sanitized header set MUST preserve Codex continuity headers such as `session_id`, `x-codex-session-id`, and `x-codex-turn-state` when those headers are required for affinity.

#### Scenario: HTTP bridge create filters HTTP request headers

- **GIVEN** an HTTP responses bridge request contains HTTP request headers such as `accept`, `accept-encoding`, `content-type`, `connection`, `authorization`, `cookie`, or `host`
- **WHEN** the bridge opens a new upstream responses WebSocket
- **THEN** those HTTP-only or hop-by-hop headers are not forwarded to the upstream WebSocket connector
- **AND** the continuity `session_id` header remains available for upstream affinity

#### Scenario: HTTP bridge reconnect filters HTTP request headers

- **GIVEN** an HTTP responses bridge session is reconnecting an upstream responses WebSocket
- **AND** the session stores HTTP request headers from the original downstream request
- **WHEN** reconnect prepares the upstream WebSocket headers
- **THEN** HTTP-only and hop-by-hop headers are filtered before the upstream WebSocket connector is called
- **AND** the selected `x-codex-turn-state` remains available for upstream continuity

#### Scenario: upstream WebSocket beta header excludes HTTP Responses token

- **GIVEN** a responses WebSocket connect request receives `OpenAI-Beta: responses=experimental`
- **WHEN** upstream WebSocket headers are built
- **THEN** `responses=experimental` is not forwarded
- **AND** `responses_websockets=2026-02-06` is present
