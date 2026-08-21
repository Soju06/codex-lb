# responses-api-compat Delta

## ADDED Requirements

### Requirement: Suppressed duplicate side-effect replays receive a retryable terminal failure

When a replayed side-effecting tool call is suppressed and its upstream turn subsequently reports `response.completed`, the proxy MUST deliver a `response.failed` terminal with code `duplicate_tool_call_replay_suppressed`; it MUST use the downstream response id, treat the request as non-success, persist a terminal durable HTTP-bridge operation when that transport is used, and MUST NOT penalize the upstream account for the intentionally fenced replay.

#### Scenario: HTTP bridge client receives a terminal failure

- **GIVEN** an HTTP bridge request suppresses a replayed side-effecting tool call
- **WHEN** the upstream emits `response.completed` for that replay
- **THEN** the client receives `response.failed` with code `duplicate_tool_call_replay_suppressed`
- **AND** the bridge operation is terminal rather than left pending
- **AND** the request is recorded as non-success

#### Scenario: WebSocket and direct SSE have equivalent terminal semantics

- **GIVEN** either a WebSocket or direct SSE request suppresses a replayed side-effecting tool call
- **WHEN** the upstream emits `response.completed` for that replay
- **THEN** the client receives the same `response.failed` code
- **AND** the upstream account is not penalized for the intentionally suppressed replay
