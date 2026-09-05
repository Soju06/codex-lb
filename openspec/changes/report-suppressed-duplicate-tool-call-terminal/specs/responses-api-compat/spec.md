# responses-api-compat Delta

## ADDED Requirements

### Requirement: Suppressed duplicate side-effect replays receive a dedicated terminal failure

When a replayed side-effecting tool call is suppressed and its upstream turn subsequently reports `response.completed`, the proxy MUST deliver a `response.failed` terminal with code `duplicate_tool_call_replay_suppressed`. It MUST use the downstream response id, treat the request as non-success, and MUST NOT count this intentionally fenced terminal as an HTTP bridge retry circuit failure.

#### Scenario: Direct SSE reports the dedicated terminal

- **GIVEN** a direct SSE request suppresses a replayed side-effecting tool call
- **WHEN** the upstream emits `response.completed` for that replay
- **THEN** the client receives `response.failed` with code `duplicate_tool_call_replay_suppressed`
- **AND** the request log records `duplicate_tool_call_replay_suppressed`, not `stream_incomplete`

#### Scenario: HTTP bridge reports the dedicated terminal without retry-circuit failure

- **GIVEN** an HTTP bridge request suppresses a replayed side-effecting tool call
- **WHEN** the upstream emits `response.completed` for that replay
- **THEN** the client receives `response.failed` with code `duplicate_tool_call_replay_suppressed`
- **AND** the request log records `duplicate_tool_call_replay_suppressed`
- **AND** the HTTP bridge retry circuit is not incremented for that terminal

#### Scenario: WebSocket reports the dedicated terminal

- **GIVEN** a WebSocket request suppresses a replayed side-effecting tool call
- **WHEN** the upstream emits `response.completed` for that replay
- **THEN** the downstream terminal uses `duplicate_tool_call_replay_suppressed`
- **AND** the upstream account is not penalized for the intentionally suppressed replay
